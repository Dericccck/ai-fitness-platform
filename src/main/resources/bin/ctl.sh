app_name=fitness-backend
echo "-------------------------"
echo "-------------------------"
echo "-------------------------"
echo "-------------------------"
which npm
source ~/.bash_profile
which npm
echo "`hostname`:`date`:run ctl "$@
this="${BASH_SOURCE-$0}"
current_dir=$(cd -P -- "$(dirname -- "$this")" && pwd -P)

app_dir=$(cd ${current_dir}/..;pwd)
mkdir -p ${app_dir}/tmp
mkdir -p ${app_dir}/log
mkdir -p ${app_dir}/out

cd ${app_dir}
ls -tp *.jar| grep -v '/$' | tail -n +6| xargs -I {} rm -- {}
cd ${app_dir}


jar=$(ls -t ${app_dir}/*.jar|head -1)
echo "`hostname`:`date`:$jar"

if [[ $1 == "start" ]];then
    echo "`hostname`:`date`:start app"
    if test $(ps -ef|grep -v grep|grep _${app_name}_|wc -l) -eq 0
    then
    	echo "`hostname`:`date`:start now"
    	CMD="java -cp $current_dir -Xmx512m -Xms128m -Dapp=_${app_name}_ -Djava.security.egd=file:/dev/./urandom -Djava.io.tmpdir=$app_dir/tmp -jar $jar --server.tomcat.basedir=$app_dir/tmp/ "
    	echo "`hostname`:`date`:$CMD"
    	echo ${CMD}|sh
    else
    	echo "`hostname`:`date`:start already"
    fi
fi
if [[ $1 == "stop" ]];then
    echo "`hostname`:`date`:stop app"
    max_counter=300
    counter=0
    while test $(ps -ef|grep -v grep|grep _${app_name}_|wc -l) -gt 0
    do
            (( counter = counter +1 ))

            echo "`hostname`:`date`:shutdown app:${counter}"
            curl http://localhost:8599/api/health/shutdown
            sleep 30
            curl -X POST http://localhost:8599/actuator/shutdown
            sleep 5
            echo "`hostname`:`date`:stopping app:${counter}"
            ps -ef|grep -v grep|grep _${app_name}_|awk '{print "kill "$2}'
            ps -ef|grep -v grep|grep _${app_name}_|awk '{print "kill "$2}'|sh
            if [[ ${counter} -gt ${max_counter} ]]
            then
                    ps -ef|grep -v grep|grep _${app_name}_|awk '{print "kill -9 "$2}'|sh
                    ps -ef|grep -v grep|grep _${app_name}_|awk '{print "kill -9 "$2}'|sh
            fi
            sleep 3
    done
    echo "`hostname`:`date`:stopped app"
    echo "`hostname`:`date`:clear bk"
    ls -tp *jar*bk| grep -v '/$' | tail -n +6| xargs -I {} rm -- {};

fi
if [[ $1 == "check" ]];then
    echo "`hostname`:`date`:check app"
    HEALTH=http://localhost:8599/actuator/health
    max_counter=60
    counter=1
    while test $(curl ${HEALTH} --connect-timeout 5 -m 5 -s|grep UP|wc -l) -lt 1
    do
        (( counter = counter +1 ))
        (( start = counter%3 ))
        if [[ ${start} -eq 0 ]]
        then
           bash ${current_dir}/start.sh
        fi
        if [[ ${counter} -gt ${max_counter} ]]
        then
           (( counter = 1))
           echo "too long to start"
           echo "stop it now"
           bash ${current_dir}/stop.sh
           echo "start it again"
           bash ${current_dir}/start.sh
        fi
        echo "`hostname`:`date`:$counter"
        echo ${HEALTH}
        curl ${HEALTH} --connect-timeout 1 -m 1 -s
        echo ""
        sleep 5
    done
    echo "`hostname`:`date` test now"
    python ${current_dir}/check.py
    if [[ $? -ne 0 ]]
    then
        while true
        do
            echo "test failed"
            sleep 60
        done
    fi
    echo "`hostname`:`date` test end"
    echo "`hostname`:`date` sleep now"
    sleep 20
    echo "`hostname`:`date`: done"
fi
echo "-------------------------"
echo "-------------------------"