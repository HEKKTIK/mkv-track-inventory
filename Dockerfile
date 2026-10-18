FROM debian:trixie-slim

# mkvmerge reads the header; python3 writes the CSV. Nothing else is needed,
# and nothing here can write to the library.
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 mkvtoolnix \
 && rm -rf /var/lib/apt/lists/*

COPY scan.py /opt/inventory/scan.py
RUN chmod 0555 /opt/inventory/scan.py

ENTRYPOINT ["python3", "/opt/inventory/scan.py"]
CMD ["/library"]
