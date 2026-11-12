# Demo data for A2C, copied into the site-config job by an init container.
# The data lives in openg2p-data, under oan-seed/a2c.
FROM alpine:3.20 AS data
ARG OPENG2P_DATA_REPO=https://github.com/Centre-for-Open-Societal-Systems/openg2p-data.git
# A branch, tag or commit. Jenkins passes the commit develop points at, so the clone layer is rebuilt
# when the data changes and is reused from the cache only when it has not.
ARG OPENG2P_DATA_REF=develop
RUN apk add --no-cache git \
  && git init -q /openg2p-data \
  && git -C /openg2p-data fetch -q --depth 1 "${OPENG2P_DATA_REPO}" "${OPENG2P_DATA_REF}" \
  && git -C /openg2p-data checkout -q FETCH_HEAD

FROM busybox:1.36
ARG SERVICE=a2c
ARG OPENG2P_DATA_REF=develop
LABEL org.oan.openg2p-data-ref="${OPENG2P_DATA_REF}"
COPY --from=data /openg2p-data/oan-seed/${SERVICE}/ /seed/
COPY load.py /seed/load.py
RUN chmod -R a+rX /seed
