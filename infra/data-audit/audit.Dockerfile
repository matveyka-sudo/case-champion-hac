# This local tag is accepted only after build.py checks its exact image ID.
# No Python install/sync runs here; the frozen e5 environment is inherited.
FROM waydex-full-api:e5e067e1b4d2a9021ddfa1d91bf918957a94a203

USER 0:0
COPY libexpat1_2.5.0-1+deb12u5_arm64.deb /tmp/audit-libexpat1.deb
RUN echo '4a733e7f636de4a4483e3e5d7b02dc8e788b115361a04dd58df89461ce2ae65a  /tmp/audit-libexpat1.deb' | sha256sum -c - \
    && dpkg -i /tmp/audit-libexpat1.deb \
    && rm /tmp/audit-libexpat1.deb

LABEL org.waydex.audit.base-image-id="sha256:a6ca2a64d1c33e359810a373ab0b2f672e5cba87b6cf3e0e72aed21597c6f61a" \
      org.waydex.audit.native-package="libexpat1:arm64=2.5.0-1+deb12u5"
ENV PROJ_NETWORK=OFF
USER 10001:10001
ENTRYPOINT ["/app/.venv/bin/python"]
CMD ["--version"]
