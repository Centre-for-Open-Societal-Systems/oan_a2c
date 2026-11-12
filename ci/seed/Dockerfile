# Demo data for A2C, copied into the site-config job by an init container.
# The data lives in openg2p-data, under oan-seed/a2c.
FROM alpine:3.20 AS data
ARG OPENG2P_DATA_REPO=https://github.com/Centre-for-Open-Societal-Systems/openg2p-data.git
ARG OPENG2P_DATA_BRANCH=develop
RUN apk add --no-cache git && git clone --depth 1 --branch "${OPENG2P_DATA_BRANCH}" "${OPENG2P_DATA_REPO}" /openg2p-data

FROM busybox:1.36
ARG SERVICE=a2c
COPY --from=data /openg2p-data/oan-seed/${SERVICE}/ /seed/
COPY load.py /seed/load.py
RUN chmod -R a+rX /seed
