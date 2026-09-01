# Use the official Python 3.12 slim image as a parent image
FROM python:3.12-slim

# Set the working directory in the container to /app
WORKDIR /app

# Copy the dependency files to the working directory
COPY pyproject.toml poetry.lock* ./

# Install poetry and configure it to install packages to the system python
RUN pip install poetry
RUN poetry config virtualenvs.create false

# Install the project dependencies
RUN poetry install --no-root --no-dev

# Copy the rest of the application's source code from your host to your image filesystem.
COPY . .

# Install the project in editable mode
RUN poetry install

# Set the default command to run when the container starts
CMD ["/bin/bash"]
