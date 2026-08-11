#!/usr/bin/env bash
this="${BASH_SOURCE-$0}"
cdir=$(cd -P -- "$(dirname -- "$this")" && pwd -P)
cd $cdir

bash ctl.sh check >>../check.log 2>&1