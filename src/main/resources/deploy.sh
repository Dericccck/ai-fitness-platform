app_name=fitness-backend
app_dir=/home/frog/app/$app_name

cd $app_dir

unzip deploy/*.jar "BOOT-INF/classes/bin/*" -d deploy/
rm -rf bin
mv deploy/BOOT-INF/classes/bin ./
chmod +X bin/*.sh

bash bin/stop.sh;
cp deploy/*.jar ./;

rm -rf *.$(date "+%Y-%m-%d-%H%M%S").bk;
find . -maxdepth 1 -type f -name '*.jar' -exec sh -c 'x="{}"; cp "$x" "${x}.$(date "+%Y-%m-%d-%H%M%S").bk"' \;
