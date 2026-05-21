# Frappe Docker Build Guide - Custom Forks

This guide explains how to build Frappe Docker images using your forked repositories and the branch naming constraints.

---

## Important Finding: FRAPPE_BRANCH Limitation

⚠️ **Critical**: The `--build-arg=FRAPPE_BRANCH` parameter in the docker build command **CANNOT** use custom branch names like `release`. It **MUST** use official version tags.

### Why This Limitation Exists

The `FRAPPE_BRANCH` argument is used to pull base Docker images from Docker Hub:
- `FROM frappe/build:${FRAPPE_BRANCH}` 
- `FROM frappe/base:${FRAPPE_BRANCH}`

These base images only exist with version-specific tags on Docker Hub:
- ✅ `frappe/build:version-15`
- ✅ `frappe/build:version-14`
- ✅ `frappe/build:version-13`
- ❌ `frappe/build:release` (does not exist)

**Therefore**: Only the Frappe framework base image is constrained to version tags. All other apps in `apps.json` can use any branch name from your forks.

---

## Prerequisites

1. Forked Frappe repositories to `casavaventures` organization
2. Docker installed and running
3. Git Bash (Windows) or Terminal (Linux/Mac)

---

## Step 1: Create apps.json

Create an `apps.json` file with your custom apps and branches:

```json
[
  {
    "url": "https://github.com/casavaventures/erpnext",
    "branch": "release"
  },
  {
    "url": "https://<YOUR_TOKEN>@github.com/casavaventures/bhm-utilities.git",
    "branch": "release"
  },
  {
    "url": "https://github.com/casavaventures/erpnext-expense-management-module.git",
    "branch": "release"
  },
  {
    "url": "https://github.com/casavaventures/hrms",
    "branch": "release"
  },
  {
    "url": "https://github.com/casavaventures/payments",
    "branch": "release"
  },
  {
    "url": "https://github.com/casavaventures/webshop",
    "branch": "release"
  },
  {
    "url": "https://<YOUR_TOKEN>@github.com/casavaventures/shopbridge.git",
    "branch": "release"
  },
  {
    "url": "https://github.com/casavaventures/india-compliance.git",
    "branch": "release"
  },
  {
    "url": "https://github.com/casavaventures/insights",
    "branch": "release"
  },
  {
    "url": "https://github.com/casavaventures/ecommerce_integrations",
    "branch": "release"
  },
  {
    "url": "https://github.com/casavaventures/frappe_whatsapp",
    "branch": "release"
  }
]
```

**Note**: 
- Apps in `apps.json` can use any branch name including `release`, `develop`, `main`, etc.
- For private repositories, replace `<YOUR_TOKEN>` with your GitHub Personal Access Token

---

## Step 2: Version Management

### Option 1: Manual Versioning (Simple)

Set the version as an environment variable before building:

**Git Bash:**
```bash
export BUILD_VERSION="1.0.1"
```

**PowerShell:**
```powershell
$BUILD_VERSION = "1.0.1"
```

Then use it in your build command (see Step 4).

### Option 2: Auto-Increment with File (Recommended)

Create a `version.txt` file to track your build version:

```bash
echo "1.0.0" > version.txt
```

**Git Bash Script (increment_version.sh):**
```bash
#!/bin/bash

# Read current version
CURRENT_VERSION=$(cat version.txt)

# Split version into components
IFS='.' read -r -a VERSION_PARTS <<< "$CURRENT_VERSION"
MAJOR="${VERSION_PARTS[0]}"
MINOR="${VERSION_PARTS[1]}"
PATCH="${VERSION_PARTS[2]}"

# Increment patch version
PATCH=$((PATCH + 1))

# Create new version
NEW_VERSION="$MAJOR.$MINOR.$PATCH"

# Save new version
echo "$NEW_VERSION" > version.txt

echo "Version incremented: $CURRENT_VERSION -> $NEW_VERSION"
export BUILD_VERSION=$NEW_VERSION
```

**PowerShell Script (increment_version.ps1):**
```powershell
# Read current version
$CurrentVersion = Get-Content version.txt

# Split version into components
$VersionParts = $CurrentVersion.Split('.')
$Major = [int]$VersionParts[0]
$Minor = [int]$VersionParts[1]
$Patch = [int]$VersionParts[2]

# Increment patch version
$Patch++

# Create new version
$NewVersion = "$Major.$Minor.$Patch"

# Save new version
Set-Content -Path version.txt -Value $NewVersion

Write-Host "Version incremented: $CurrentVersion -> $NewVersion" -ForegroundColor Green
$env:BUILD_VERSION = $NewVersion
```

### Option 3: Timestamp-Based Versioning

Use timestamp for unique versions:

**Git Bash:**
```bash
export BUILD_VERSION="1.0.$(date +%Y%m%d%H%M%S)"
# Example: 1.0.20251229115030
```

**PowerShell:**
```powershell
$BUILD_VERSION = "1.0.$(Get-Date -Format 'yyyyMMddHHmmss')"
# Example: 1.0.20251229115030
```

### Option 4: Git Commit-Based Versioning

Use Git commit hash as version:

**Git Bash:**
```bash
export BUILD_VERSION="1.0.0-$(git rev-parse --short HEAD)"
# Example: 1.0.0-abc1234
```

**PowerShell:**
```powershell
$BUILD_VERSION = "1.0.0-$(git rev-parse --short HEAD)"
# Example: 1.0.0-abc1234
```

---

## Step 3: Encode apps.json to Base64

### Git Bash (Windows/Linux/Mac)

```bash
export APPS_JSON_BASE64=$(base64 -w 0 apps.json)
```

### PowerShell (Windows)

```powershell
$APPS_JSON_BASE64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes((Get-Content -Path apps.json -Raw)))
```

Verify the encoding:

**Git Bash:**
```bash
echo $APPS_JSON_BASE64
```

**PowerShell:**
```powershell
Write-Output $APPS_JSON_BASE64
```

---

## Step 4: Build Docker Image with Version

### Git Bash (Windows/Linux/Mac)

```bash
docker build --no-cache \
  --build-arg=FRAPPE_PATH=https://github.com/casavaventures/frappe \
  --build-arg=FRAPPE_BRANCH=version-15 \
  --build-arg=APPS_JSON_BASE64=$APPS_JSON_BASE64 \
  --tag=erp-ventures:$BUILD_VERSION \
  --tag=erp-ventures:latest \
  --file=images/layered/Containerfile .
```

### PowerShell (Windows)

```powershell
docker build --no-cache `
  --build-arg=FRAPPE_PATH=https://github.com/casavaventures/frappe `
  --build-arg=FRAPPE_BRANCH=version-15 `
  --build-arg=APPS_JSON_BASE64=$APPS_JSON_BASE64 `
  --tag=erp-ventures:$BUILD_VERSION `
  --tag=erp-ventures:latest `
  --file=images/layered/Containerfile .
```

**Important Notes:**
- `FRAPPE_PATH` can point to your fork: `https://github.com/casavaventures/frappe`
- `FRAPPE_BRANCH` **MUST** be a version tag: `version-15`, `version-14`, etc.
- ❌ **CANNOT** use: `FRAPPE_BRANCH=release` (will fail - base image doesn't exist)
- ✅ **Apps in apps.json CAN** use `release` branch
- `$BUILD_VERSION` will be replaced with your version number

---

## Step 5: Verify Build

Check if the image was created successfully:

**Git Bash & PowerShell:**
```bash
docker images | grep erp-ventures
```

Or:
```bash
docker images erp-ventures
```

Expected output:
```
REPOSITORY      TAG       IMAGE ID       CREATED         SIZE
erp-ventures    1.0.5     abc123def456   2 minutes ago   2.5GB
erp-ventures    latest    abc123def456   2 minutes ago   2.5GB
```

---

## Branch Strategy Summary

| Component | Branch Flexibility | Example |
|-----------|-------------------|---------|
| **Frappe Framework (base image)** | ❌ Must use version tags | `version-15`, `version-14` |
| **Custom apps (in apps.json)** | ✅ Can use any branch | `release`, `develop`, `main`, `custom-branch` |

### Why This Design?

1. **Frappe Base Image**: The Docker build process needs pre-built base images from Docker Hub that contain the core Frappe framework dependencies and environment. These images are only published with version tags.

2. **Custom Apps**: These are cloned from GitHub during the build process, so they can use any branch that exists in your repository.

---

## Troubleshooting

### Error: "frappe/build:release: not found"

**Problem**: Using a non-existent Docker base image tag.

**Solution**: Change `FRAPPE_BRANCH=release` to `FRAPPE_BRANCH=version-15`

### Error: "Could not find branch 'release' in repository"

**Problem**: One of your apps in `apps.json` doesn't have a `release` branch.

**Solution**: Check each repository and either:
- Create the missing branch, or
- Update `apps.json` to use an existing branch name

### Check Available Branches in Your Fork

**Git Bash:**
```bash
git ls-remote --heads https://github.com/casavaventures/frappe
```

**PowerShell:**
```powershell
git ls-remote --heads https://github.com/casavaventures/frappe
```

---

## Quick Reference

### Check Docker Hub for Available Frappe Versions

Visit: https://hub.docker.com/r/frappe/build/tags

### Common Frappe Versions
- `version-15` - Latest stable (recommended)
- `version-14` - Previous stable
- `version-13` - Older stable
- `develop` - Development branch (unstable)

---

## Complete Build Scripts with Auto-Versioning

### Git Bash Script (build.sh)

```bash
#!/bin/bash

# Exit on error
set -e

echo "=== Frappe Docker Build Script ==="

# Step 1: Increment version
if [ -f version.txt ]; then
    CURRENT_VERSION=$(cat version.txt)
    IFS='.' read -r -a VERSION_PARTS <<< "$CURRENT_VERSION"
    MAJOR="${VERSION_PARTS[0]}"
    MINOR="${VERSION_PARTS[1]}"
    PATCH="${VERSION_PARTS[2]}"
    PATCH=$((PATCH + 1))
    BUILD_VERSION="$MAJOR.$MINOR.$PATCH"
    echo "$BUILD_VERSION" > version.txt
    echo "Version incremented: $CURRENT_VERSION -> $BUILD_VERSION"
else
    BUILD_VERSION="1.0.0"
    echo "$BUILD_VERSION" > version.txt
    echo "Created initial version: $BUILD_VERSION"
fi

# Step 2: Encode apps.json
echo "Encoding apps.json..."
export APPS_JSON_BASE64=$(base64 -w 0 apps.json)

# Step 3: Build image
echo "Building Docker image with version: $BUILD_VERSION"
docker build --no-cache \
  --build-arg=FRAPPE_PATH=https://github.com/casavaventures/frappe \
  --build-arg=FRAPPE_BRANCH=version-15 \
  --build-arg=APPS_JSON_BASE64=$APPS_JSON_BASE64 \
  --tag=erp-ventures:$BUILD_VERSION \
  --tag=erp-ventures:latest \
  --file=images/layered/Containerfile .

# Step 4: Verify
echo "Verifying build..."
docker images erp-ventures

echo "=== Build Complete ==="
echo "Built version: $BUILD_VERSION"
```

Make executable and run:
```bash
chmod +x build.sh
./build.sh
```

### PowerShell Script (build.ps1)

```powershell
# Exit on error
$ErrorActionPreference = "Stop"

Write-Host "=== Frappe Docker Build Script ===" -ForegroundColor Green

# Step 1: Increment version
if (Test-Path version.txt) {
    $CurrentVersion = Get-Content version.txt
    $VersionParts = $CurrentVersion.Split('.')
    $Major = [int]$VersionParts[0]
    $Minor = [int]$VersionParts[1]
    $Patch = [int]$VersionParts[2]
    $Patch++
    $BUILD_VERSION = "$Major.$Minor.$Patch"
    Set-Content -Path version.txt -Value $BUILD_VERSION
    Write-Host "Version incremented: $CurrentVersion -> $BUILD_VERSION" -ForegroundColor Cyan
} else {
    $BUILD_VERSION = "1.0.0"
    Set-Content -Path version.txt -Value $BUILD_VERSION
    Write-Host "Created initial version: $BUILD_VERSION" -ForegroundColor Cyan
}

# Step 2: Encode apps.json
Write-Host "Encoding apps.json..." -ForegroundColor Yellow
$APPS_JSON_BASE64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes((Get-Content -Path apps.json -Raw)))

# Step 3: Build image
Write-Host "Building Docker image with version: $BUILD_VERSION" -ForegroundColor Yellow
docker build --no-cache `
  --build-arg=FRAPPE_PATH=https://github.com/casavaventures/frappe `
  --build-arg=FRAPPE_BRANCH=version-15 `
  --build-arg=APPS_JSON_BASE64=$APPS_JSON_BASE64 `
  --tag=erp-ventures:$BUILD_VERSION `
  --tag=erp-ventures:latest `
  --file=images/layered/Containerfile .

# Step 4: Verify
Write-Host "Verifying build..." -ForegroundColor Yellow
docker images erp-ventures

Write-Host "=== Build Complete ===" -ForegroundColor Green
Write-Host "Built version: $BUILD_VERSION" -ForegroundColor Green
```

Run:
```powershell
.\build.ps1
```

---

## Version Management Workflow

### First Build
```bash
./build.sh  # Creates version.txt with 1.0.0
# Builds: erp-ventures:1.0.0
```

### Second Build
```bash
./build.sh  # Reads 1.0.0, increments to 1.0.1
# Builds: erp-ventures:1.0.1
```

### Third Build
```bash
./build.sh  # Reads 1.0.1, increments to 1.0.2
# Builds: erp-ventures:1.0.2
```

### Manual Version Bump

To change major or minor version, edit `version.txt`:

**For minor version bump (1.0.x → 1.1.0):**
```bash
echo "1.1.0" > version.txt
```

**For major version bump (1.x.x → 2.0.0):**
```bash
echo "2.0.0" > version.txt
```

---

## List All Built Versions

**Git Bash & PowerShell:**
```bash
docker images erp-ventures --format "table {{.Repository}}	{{.Tag}}	{{.CreatedAt}}	{{.Size}}"
```

Example output:
```
REPOSITORY      TAG       CREATED AT              SIZE
erp-ventures    1.0.3     2025-12-29 11:50:00     2.5GB
erp-ventures    latest    2025-12-29 11:50:00     2.5GB
erp-ventures    1.0.2     2025-12-28 15:30:00     2.5GB
erp-ventures    1.0.1     2025-12-27 10:20:00     2.5GB
```

---

## Clean Up Old Versions

Remove specific version:
```bash
docker rmi erp-ventures:1.0.1
```

Remove all except latest:
```bash
docker images erp-ventures --format "{{.Tag}}" | grep -v "latest" | xargs -I {} docker rmi erp-ventures:{}
```

---

## Next Steps

After successful build:
1. Tag with registry path
2. Push image to registry (Docker Hub, AWS ECR, etc.)
3. Deploy using docker-compose
4. Create AMI from running instance
5. Restore site backup

---

## Key Takeaways

✅ **FRAPPE_BRANCH** must use version tags (`version-15`)
✅ **Apps in apps.json** can use any branch name (`release`)
✅ Only Frappe framework base image has this limitation
✅ All other apps are fetched directly from GitHub and have no restrictions
✅ Use `<YOUR_TOKEN>` placeholder for private repositories - replace with actual GitHub Personal Access Token
✅ Version auto-increments with each build using `version.txt`
✅ Both scripts create `version.txt` if it doesn't exist (starting at 1.0.0)
