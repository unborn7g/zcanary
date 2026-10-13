#!/usr/bin/env bash
# usage: rpc.sh <url> method [json-params]
url=$1; m=$2; p=${3:-[]}
curl -s -u ${RPC_USER:-zebra}:${RPC_PASS:-zebra} -X POST -H 'Content-Type: application/json' -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"$m\",\"params\":$p}" "$url"
