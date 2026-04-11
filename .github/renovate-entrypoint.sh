#!/bin/bash

apt update

apt install -y build-essential libpq-dev

runuser --preserve-env=RENOVATE_REPOSITORIES -u ubuntu renovate