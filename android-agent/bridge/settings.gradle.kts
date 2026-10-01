// Bridge app (Layer 1). `core` is plain Kotlin/JVM and holds all protocol and
// decision logic; `app` is the thin Android layer and needs the Android SDK.
// Build the app with: ./gradlew -Pandroid=true :app:assembleDebug
pluginManagement {
    repositories {
        google {
            content {
                includeGroupByRegex("com\\.android.*")
                includeGroupByRegex("com\\.google.*")
                includeGroupByRegex("androidx.*")
            }
        }
        mavenCentral()
        gradlePluginPortal()
    }
    plugins {
        kotlin("jvm") version "2.0.21"
        kotlin("android") version "2.0.21"
        id("com.android.application") version "8.7.3"
    }
}

dependencyResolutionManagement {
    repositories {
        google {
            content {
                includeGroupByRegex("com\\.android.*")
                includeGroupByRegex("com\\.google.*")
                includeGroupByRegex("androidx.*")
            }
        }
        mavenCentral()
    }
}

rootProject.name = "agent-bridge"
include(":core")
if (providers.gradleProperty("android").orNull == "true") {
    include(":app")
}
