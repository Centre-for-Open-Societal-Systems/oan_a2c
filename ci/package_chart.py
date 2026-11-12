"""Package helm/oan-a2c-service with this build's image, and publish that archive to oan-helm.

Run by the Jenkins stages "Package chart" and "Publish chart" of every develop build:

    package_chart.py version   prints the chart version, which is also the image tag of the build:
                               <__version__ of oan_a2c>-develop.<BUILD_NUMBER>, e.g. 0.0.0-develop.57 (develop only)
    package_chart.py package   writes built-chart/oan-a2c-service-<version>.tgz
    package_chart.py publish   adds that archive to repository/develop/ of oan-helm

The Chart.yaml annotations and the publisher come from scripts/ci of oan-helm, so this chart is listed like the registry
charts. The version is 0.0.0-develop.N, which publish_chart.py sends to repository/develop/. The chart keeps its frappe-service dependency vendored in helm/oan-a2c-service/charts,
so, unlike the registry charts, nothing is downloaded.

version needs BRANCH_NAME (develop) and BUILD_NUMBER. package needs IMAGE_REPOSITORY (registry/repository, no tag), IMAGE_TAG, SOURCE_COMMIT, BRANCH_NAME and COMMIT_SUBJECT.
publish needs GH_TOKEN with push access to oan-helm. DRY_RUN=1 publishes nothing. Standard library only.

Exit codes: 0 done (or already published by an earlier run of this commit), 1 failure, 3 no credential.
"""

import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHART = "oan-a2c-service"
OAN_HELM_SLUG = "Centre-for-Open-Societal-Systems/oan-helm"
OAN_HELM = "https://github.com/%s.git" % OAN_HELM_SLUG
ALREADY_PUBLISHED = 4
OUTPUT = ROOT / "built-chart"


def run(command, cwd=None, env=None):
	return subprocess.run(
		command, cwd=str(cwd or ROOT), env=env, check=True, stdout=subprocess.PIPE, text=True
	).stdout


def fetch_publisher(work):
	"""scripts/ci of oan-helm main, without its repository/ archives."""
	target = work / "oan-helm"
	run(["git", "clone", "--quiet", "--depth", "1", "--filter=blob:none", "--sparse", OAN_HELM, str(target)])
	run(["git", "sparse-checkout", "set", "scripts/ci"], cwd=target)
	return target / "scripts/ci"


def load_generic(work):
	scripts = fetch_publisher(work)
	sys.path.insert(0, str(scripts))
	import package_chart as generic  # oan-helm's, fetched above

	return generic


def app_version():
	match = re.search(
		r'(?m)^__version__ = "(\d+\.\d+\.\d+)"$',
		(ROOT / "oan_a2c" / "__init__.py").read_text(encoding="utf-8"),
	)
	if not match:
		raise ValueError('no __version__ = "N.N.N" in oan_a2c/__init__.py')
	return match.group(1)


def chart_version(env):
	"""<__version__>-develop.<BUILD_NUMBER>, a version publish_chart.py puts in repository/develop/."""
	if env["BRANCH_NAME"] != "develop":
		raise ValueError("only develop packages the chart, not %s" % env["BRANCH_NAME"])
	return "%s-develop.%s" % (app_version(), env["BUILD_NUMBER"])


def version_build(env, work):
	print(chart_version(env))
	return 0


def package_build(env, work):
	generic = load_generic(work)
	version = chart_version(env)
	if env["IMAGE_TAG"] != version:
		raise ValueError("image tag %s is not the chart version %s" % (env["IMAGE_TAG"], version))
	chart = work / CHART
	shutil.copytree(str(ROOT / "helm" / CHART), str(chart))
	values = chart / "values.yaml"
	values.write_text(
		generic.set_image(
			values.read_text(encoding="utf-8"), "frappe.image", env["IMAGE_REPOSITORY"], env["IMAGE_TAG"]
		),
		encoding="utf-8",
	)
	metadata = chart / "Chart.yaml"
	metadata.write_text(
		generic.annotate(
			metadata.read_text(encoding="utf-8"),
			{
				"oan.org/source-repository": "Centre-for-Open-Societal-Systems/oan_a2c",
				"oan.org/source-commit": env["SOURCE_COMMIT"],
				"oan.org/source-subject": env.get("COMMIT_SUBJECT", "")[:200],
				"oan.org/release-name": "a2c",
				"oan.org/release-namespace": "a2c-develop",
				"oan.org/values-overlay": "helm/environments/dev/oan-a2c-service.yaml",
			},
		),
		encoding="utf-8",
	)
	shutil.rmtree(str(OUTPUT), ignore_errors=True)
	OUTPUT.mkdir()
	run(["helm", "package", str(chart), "--version", version, "--destination", str(OUTPUT)])
	print("packaged %s-%s.tgz" % (CHART, version))
	return 0


def published_commit(env, name):
	"""oan.org/source-commit of the archive already in repository/develop/ of oan-helm, or None."""
	request = urllib.request.Request(
		"https://api.github.com/repos/%s/contents/repository/develop/%s" % (OAN_HELM_SLUG, name),
		headers={
			"Accept": "application/vnd.github.raw",
			"Authorization": "Bearer " + env.get("GH_TOKEN", ""),
		},
	)
	try:
		with urllib.request.urlopen(request) as response:
			data = response.read()
		with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
			text = archive.extractfile("%s/Chart.yaml" % CHART).read().decode("utf-8")
	except (OSError, KeyError, tarfile.TarError):
		return None
	match = re.search(r"oan\.org/source-commit: *\"?([0-9a-f]{7,40})", text)
	return match.group(1) if match else None


def publish_build(env, work):
	if env.get("DRY_RUN") != "1" and not env.get("GH_TOKEN"):
		print("FAIL: no GitHub credential: the chart was not published.")
		return 3
	archives = sorted(OUTPUT.glob("*.tgz"))
	if len(archives) != 1:
		raise ValueError("expected the one archive of this build in %s" % OUTPUT)
	scripts = fetch_publisher(work)
	result = subprocess.run([sys.executable, str(scripts / "publish_chart.py"), str(archives[0])], env=env)
	if result.returncode == ALREADY_PUBLISHED:
		# Build numbers can repeat (job recreated), so a taken version is only fine if it holds this commit.
		if published_commit(env, archives[0].name) == env["SOURCE_COMMIT"]:
			print("%s was published by an earlier run of this commit." % archives[0].name)
			return 0
		print(
			"FAIL: %s is already published for another commit; the chart was not published."
			% archives[0].name
		)
		return 1
	return result.returncode


def main(argv=None, env=None):
	argv = sys.argv[1:] if argv is None else argv
	env = dict(os.environ if env is None else env)
	actions = {"version": version_build, "package": package_build, "publish": publish_build}
	if len(argv) != 1 or argv[0] not in actions:
		print("usage: package_chart.py version|package|publish")
		return 1
	work = Path(tempfile.mkdtemp(prefix="a2c-chart-"))
	try:
		return actions[argv[0]](env, work)
	except (KeyError, ValueError, OSError, subprocess.CalledProcessError) as error:
		print("FAIL: %s: %s" % (error.__class__.__name__, error))
		return 1
	finally:
		shutil.rmtree(str(work), ignore_errors=True)


if __name__ == "__main__":
	sys.exit(main())
