ARG JAVA_BUILD_IMAGE=maven:3.9.9-eclipse-temurin-8
ARG JAVA_RUNTIME_IMAGE=eclipse-temurin:8-jre-jammy
FROM ${JAVA_BUILD_IMAGE} AS build
WORKDIR /build
COPY pom.xml ./
RUN --mount=type=cache,target=/root/.m2 mvn -B -q dependency:go-offline
COPY src ./src
RUN --mount=type=cache,target=/root/.m2 mvn -B -q -DskipTests package

FROM ${JAVA_RUNTIME_IMAGE}
WORKDIR /service
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid 10001 fitness && useradd --uid 10001 --gid 10001 --no-create-home fitness
COPY --from=build --chown=fitness:fitness /build/target/fitness-booking-service.jar /service/app.jar
USER 10001:10001
EXPOSE 8083
STOPSIGNAL SIGTERM
ENTRYPOINT ["java", "-XX:+ExitOnOutOfMemoryError", "-jar", "/service/app.jar"]
