FROM ghcr.io/home-assistant/base:latest

ARG BUILD_ARCH
ARG BUILD_VERSION

LABEL \
  io.hass.name="Home Command Bridge" \
  io.hass.description="Securely connects Home Assistant to Home Command." \
  io.hass.arch="${BUILD_ARCH}" \
  io.hass.type="app" \
  io.hass.version="${BUILD_VERSION}"

RUN apk add --no-cache python3 py3-aiohttp

COPY bridge.py /opt/home-command/bridge.py
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]
