FROM docker.io/coturn/coturn:4.18.0-r0-alpine
USER root
# The upstream binary carries file capabilities for privileged ports. This
# service uses high ports and runs with an empty capability bounding set.
RUN apk add --no-cache python3 \
    && install -m 0755 /usr/bin/turnserver /usr/bin/turnserver.unprivileged \
    && mv /usr/bin/turnserver.unprivileged /usr/bin/turnserver \
    && mkdir -p /data /opt/vibedesk \
    && chown 65534:65534 /data
COPY relay.py check_access.py /opt/vibedesk/
COPY scripts/endpoint.py /opt/vibedesk/scripts/
COPY LICENSE /usr/share/licenses/vibedesk-relay/LICENSE
USER 65534:65534
ENTRYPOINT ["python3", "/opt/vibedesk/relay.py"]
CMD ["serve"]
