#!/usr/bin/env bash
this="${BASH_SOURCE-$0}"
cdir=$(cd -P -- "$(dirname -- "$this")" && pwd -P)
cd $cdir
nohup bash ctl.sh start >>../ctl.log 2>&1 &
