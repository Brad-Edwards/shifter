# Shared participant OpenVPN pool server (#2480): OpenVPN plus its authorizing
# controller. The container runs in its own network namespace on a pool VM:
# NET_ADMIN (tunnel, routes, nftables) applies to that namespace only, and
# OpenVPN drops to nobody after it opens the tunnel and reads its credentials.
FROM alpine:3.22@sha256:5291449c3df73caf6ed85e649dec1b9e818b39a5d8c871e97afc13e9cd5e8fa8

# Minor-version constraints: CI rebuilds without cache pick up patch releases.
RUN apk add --no-cache "openvpn~2.6" "nftables~1.1" "python3~3.12"

WORKDIR /opt/shifter-openvpn
COPY shifter_openvpn ./shifter_openvpn

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 1194/udp 8080/tcp
ENTRYPOINT ["python3", "-m", "shifter_openvpn"]
