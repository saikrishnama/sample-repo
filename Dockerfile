FROM amazonlinux:latest
LABEL Image="LinuxBase",appname="baseapp"
# MAINTAINER "mandava"
RUN yum update -y 
RUN yum install shadow-utils -y
RUN yum install sudo -y
RUN useradd  -m -s /bin/bash myappuser
ENV HTTP_PROXY=""
ENV HTTPS_PROXY=""
EXPOSE 80 
RUN mkdir /app

USER myappuser
WORKDIR /app 
VOLUME /app
# CMD [ "ls", "-ltr" ]
ENTRYPOINT [ "ls", "-ltr" ]
