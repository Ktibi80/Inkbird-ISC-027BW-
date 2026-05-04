ARG BUILD_FROM
FROM python:3.11-alpine

RUN pip3 install --no-cache-dir tinytuya paho-mqtt

COPY inkbird_bridge.py /

CMD [ "python3", "/inkbird_bridge.py" ]
