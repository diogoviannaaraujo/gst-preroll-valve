FROM rust:bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    RUSTUP_HOME=/usr/local/rustup \
    CARGO_HOME=/usr/local/cargo \
    PATH=/usr/local/cargo/bin:/usr/local/rustup/bin:/usr/local/bin:/usr/bin:/bin

WORKDIR /app

# System deps for Rust + GStreamer + Python bindings
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    build-essential \
    pkg-config \
    python3 \
    python3-gi \
    python3-pip \
    gobject-introspection \
    libgirepository1.0-dev \
    gir1.2-gstreamer-1.0 \
    gstreamer1.0-tools \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-plugins-ugly \
    gstreamer1.0-libav \
    libgstreamer1.0-dev \
    libgstreamer-plugins-base1.0-dev \
    libgstreamer-plugins-bad1.0-dev \
    && rm -rf /var/lib/apt/lists/*

# Cache-friendly build steps: first copy manifests, then source
COPY Cargo.toml Cargo.lock /app/
COPY src /app/src

RUN cargo build --release

# Copy test script
COPY test_plugin.py /app/test_plugin.py

# Make built plugin discoverable for GStreamer
ENV GST_PLUGIN_PATH=/app/target/release

# Default command runs the Python test
CMD ["python3", "/app/test_plugin.py"]

