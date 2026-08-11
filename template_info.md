# 源码自动生成模板 spring-boot-demo

### 概述

* 模板: spring-boot-demo
* 模板使用时间: 2018-11-22 17:39:58

### Docker
* Image: registry.cn-beijing.aliyuncs.com/rdc-template/spring-boot
* Tag: 3.0
* SHA256: 1f7d69c45529843a76ae849be2e4820e506e94637c1b084035748bc4abbb5bcb

### 用户输入参数
* repoUrl: "git@code.aliyun.com:shuyiwa/fitness-backend.git" 
* needDockerfile: "N" 
* appName: "fitness-backend" 
* operator: "aliyun_757325" 
* appReleaseContent: "# 
* 请参考: 请参考 
* https://help.aliyun.com/document_detail/59293.html: https://help.aliyun.com/document_detail/59293.html 
* 了解更多关于release文件的编写方式: 了解更多关于release文件的编写方式 
* [NEWLINE][NEWLINE]#: [NEWLINE][NEWLINE]# 
* 构建源码语言类型[NEWLINE]code.language: oracle-jdk1.8[NEWLINE][NEWLINE]# 
* 构建打包使用的打包文件[NEWLINE]build.output: target/fitness-backend.jar" 

### 上下文参数
* appName: fitness-backend
* operator: aliyun_757325
* gitUrl: git@code.aliyun.com:shuyiwa/fitness-backend.git
* branch: master


### 命令行
	sudo docker run --rm -v `pwd`:/workspace -e repoUrl="git@code.aliyun.com:shuyiwa/fitness-backend.git" -e needDockerfile="N" -e appName="fitness-backend" -e operator="aliyun_757325" -e appReleaseContent="# 请参考 https://help.aliyun.com/document_detail/59293.html 了解更多关于release文件的编写方式 [NEWLINE][NEWLINE]# 构建源码语言类型[NEWLINE]code.language=oracle-jdk1.8[NEWLINE][NEWLINE]# 构建打包使用的打包文件[NEWLINE]build.output=target/fitness-backend.jar"  registry.cn-beijing.aliyuncs.com/rdc-template/spring-boot:3.0

