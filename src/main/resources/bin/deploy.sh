#!/usr/bin/env bash
this="${BASH_SOURCE-$0}"
cdir=$(cd -P -- "$(dirname -- "$this")" && pwd -P)
cd $cdir
app_dir=$(cd ${cdir}/..;pwd)
cd $app_dir
newJar=$app_dir/target/fitness-backend.jar

if test -f "$newJar"; then
    echo "$newJar exist"
    cd $cdir
    bash ctl.sh stop
    cd $app_dir

    cp $newJar ./;
    rm -rf *.$(date "+%Y-%m-%d-%H%M%S").bk;
    find . -maxdepth 1 -type f -name '*.jar' -exec sh -c 'x="{}"; cp -a "$x" "${x}.$(date "+%Y-%m-%d-%H%M%S").bk"' \;

    cd $cdir
    bash start.sh
    bash check.sh
fi