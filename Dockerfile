FROM amazonlinux:latest
LABEL Image="LinuxBase",appname="baseapp"
# MAINTAINER "mandava"
RUN yum update -y
RUN yum install shadow-utils -y
RUN yum install sudo -y
RUN useradd  -m -s /bin/bash myappuser

RUN mkdir /app

USER myappuser
WORKDIR /app 
VOLUME /app
CMD [ "ls", "-ltr" ]
# WORKDIR /home/myappuser
# ADD  compose.yaml /home/myappuser/
# ADD https://github.com/saikrishnama/sample-repo.git .


# docker run --mount type=bind,source=/host/path,target=/container/path my-image

# ADD /Users/smandava/Documents/fluxcd-repo/flux-lab-poc/app.py .
# ADD ../app.py .