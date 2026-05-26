# WebP Converter 🖼️

[![Ukrainian Translation](https://img.shields.io/badge/Переклад-Українська-blue.svg)](README_UA.md)

A fast, modern, and user-friendly image converter designed to batch-process PNG files into the WebP format. Built with a focus on simplicity, speed, and granular control over compression.

## Features
* **Modern GUI:** A clean, custom-styled interface built with `tkinter`.
* **Drag & Drop:** Effortless file and folder importing (Windows-optimized).
* **Flexible Compression:**
    * **Quality Slider:** Adjust output quality (10-100%).
    * **Lossless Mode:** Perfect for logos and pixel art.
    * **Compression Methods (0-6):** Balance encoding speed vs. final file size.
* **Real-time Prediction:** Get an instant estimate of the output size before you convert.
* **Multithreaded:** Asynchronous processing keeps the interface responsive.
* **Smart Analytics:** View storage savings after processing.

## Installation

1. Clone this repository or download `webp_converter.py`.
2. Install the required dependencies:

```bash
pip install Pillow windnd