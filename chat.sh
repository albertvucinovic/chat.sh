#!/bin/bash
# run_myscript.sh

# Activate the virtual environment
source $(dirname "$0")/venv/bin/activate

#Load the env variables ..KEY, ..MODEL
source $(dirname "$0")/.env

pushd $(dirname "$0")
# Run the Python script
python $(dirname "$0")/chat.py $@

popd

# Deactivate the virtual environment
deactivate

