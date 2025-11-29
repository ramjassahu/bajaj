#!/usr/bin/env bash
set -o errexit

apt-get update
apt-get install -y tesseract-ocr libtesseract-dev tesseract-ocr-eng

tesseract --version
