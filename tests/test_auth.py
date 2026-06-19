import os
import sys

# Ensure repo root on path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from bot import verify_status_request


def test_verify_status_request_no_token_env():
    if 'STATUS_TOKEN' in os.environ:
        del os.environ['STATUS_TOKEN']
    headers = {}
    assert verify_status_request(headers, {}) is True


def test_verify_status_request_with_header():
    os.environ['STATUS_TOKEN'] = 's3cr3t'
    headers = {'Authorization': 'Bearer s3cr3t'}
    # headers in BaseHTTPRequestHandler are case-insensitive mapping; simple dict works
    assert verify_status_request(headers, {}) is True


def test_verify_status_request_with_query():
    os.environ['STATUS_TOKEN'] = 's3cr3t'
    headers = {}
    assert verify_status_request(headers, {'token': ['s3cr3t']}) is True


def test_verify_status_request_reject():
    os.environ['STATUS_TOKEN'] = 's3cr3t'
    headers = {'Authorization': 'Bearer wrong'}
    assert verify_status_request(headers, {}) is False
