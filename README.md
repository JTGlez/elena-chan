<div align="center">

<img src="assets/elena-readme.png" alt="Elena-chan" width="300">

<sub>Character art generated with AI (ChatGPT).</sub>

# Elena-chan

**Custom Stack-chan firmware and server for local AI.**

[![License: MIT](https://img.shields.io/github/license/JTGlez/elena-chan?style=for-the-badge&color=8B3A4A&labelColor=3B2A2A)](LICENSE)
[![CI](https://img.shields.io/github/actions/workflow/status/JTGlez/elena-chan/ci.yml?branch=main&style=for-the-badge&label=CI&color=D9A45B&labelColor=3B2A2A)](https://github.com/JTGlez/elena-chan/actions/workflows/ci.yml)

[![ESP-IDF v6.1](https://img.shields.io/badge/ESP--IDF-v6.1-E7352C?style=flat-square&logo=espressif&logoColor=white&labelColor=3B2A2A)](https://github.com/espressif/esp-idf/releases/tag/v6.1)
[![Python 3.10 | 3.12](https://img.shields.io/badge/Python-3.10%20%7C%203.12-D9A45B?style=flat-square&logo=python&logoColor=white&labelColor=3B2A2A)](#dependencies)
[![M5Stack CoreS3](https://img.shields.io/badge/M5Stack-CoreS3-8B3A4A?style=flat-square&labelColor=3B2A2A)](https://docs.m5stack.com/en/core/CoreS3)
[![xiaozhi-esp32](https://img.shields.io/badge/xiaozhi--esp32-4632dc5-8B3A4A?style=flat-square&logo=github&logoColor=white&labelColor=3B2A2A)](https://github.com/JTGlez/xiaozhi-esp32/tree/elena)
[![xiaozhi-esp32-server](https://img.shields.io/badge/xiaozhi--esp32--server-4745186-8B3A4A?style=flat-square&logo=github&logoColor=white&labelColor=3B2A2A)](https://github.com/JTGlez/xiaozhi-esp32-server/tree/elena)

</div>

## About

Elena-chan connects a [Stack-chan](https://github.com/stack-chan/stack-chan) robot to speech and language models on your own machine. It has two parts:

| Part | Role |
|---|---|
| **Firmware** | Runs on the robot's M5Stack CoreS3 and handles audio, the display and the servos |
| **Server** | Runs on your computer and connects the robot to local speech-to-text, language and text-to-speech models |

## Dependencies

| Dependency | Version | Used for |
|---|---|---|
| [ESP-IDF](https://github.com/espressif/esp-idf) | v6.1 | Building the firmware |
| [xiaozhi-esp32](https://github.com/78/xiaozhi-esp32) | Based on `4632dc5` | Firmware base |
| [xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server) | Based on `4745186` | Server base |
| [Python](https://www.python.org/) | 3.10 for the server, 3.12 for ESP-IDF | Server and build tools |
| [M5Stack CoreS3](https://docs.m5stack.com/en/core/CoreS3) | ESP32-S3 | Robot hardware |

## License

Elena-chan is available under the [MIT License](LICENSE). The firmware and server forks keep their own licenses.
