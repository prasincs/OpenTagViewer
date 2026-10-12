#!/usr/bin/env bash
# Build the phone APK with an explicit SDK and Python runtime.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${JAVA_HOME:?Set JAVA_HOME to JDK 17 or newer}"
: "${ANDROID_HOME:?Set ANDROID_HOME to your Android SDK}"
python_bin="${FINDMY_PYTHON:-python3}"
export PATH="$JAVA_HOME/bin:$PATH"
bash ./gradlew --no-daemon --max-workers=2 \
  -PhomeInstall=true -PotvAbi=arm64-v8a "-PpythonExecutable=$python_bin" \
  '-Dorg.gradle.jvmargs=-Xmx3g -Dfile.encoding=UTF-8' \
  :app:assembleDebug :app:testDebugUnitTest
mkdir -p out
cp app/build/outputs/apk/debug/app-debug.apk out/home-findmy.apk
(cd out && shasum -a 256 home-findmy.apk > SHA256SUMS)
