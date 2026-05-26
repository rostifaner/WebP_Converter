WebP Converter 🖼️

A fast, modern, and user-friendly image converter designed to batch-process PNG files into the WebP format.

Features

Modern GUI: Built with tkinter.

Drag & Drop: Easy file importing.

Flexible Compression: Quality slider, Lossless mode, and compression methods (0-6).

Real-time Prediction: Estimated output size.

Multithreaded: Fast processing without UI freezing.

Installation

pip install Pillow windnd


How to use

1. Run the script: python converter.py
2. Add Files: Drag and drop your PNG images directly into the window.
3. Configure: Adjust the compression settings in the sidebar.
4. Convert: Click the "Convert" button to save your files.


Technical Concept

The application uses the Pillow library to interface with libwebp for high-quality compression.