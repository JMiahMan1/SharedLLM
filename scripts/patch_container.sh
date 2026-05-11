#!/bin/bash
# scripts/patch_container.sh
CONTAINER=$1
FILE=$2
TARGET=$3

if [ -z "$CONTAINER" ] || [ -z "$FILE" ] || [ -z "$TARGET" ]; then
    echo "Usage: $0 <container_name> <source_file> <target_path>"
    exit 1
fi

ID=$(curl -s --unix-socket /var/run/docker.sock -X POST http://localhost/containers/$CONTAINER/exec -H "Content-Type: application/json" -d "{\"Cmd\": [\"sh\", \"-c\", \"cat > $TARGET\"], \"AttachStdin\": true}" | jq -r .Id)

if [ "$ID" == "null" ] || [ -z "$ID" ]; then
    echo "Failed to create exec instance"
    exit 1
fi

# Send the JSON body first, then the file content? 
# No, exec/start doesn't work that way with --data-binary.
# Actually, the Docker API for exec/start with stdin is tricky via curl.
# But we can try to send it as a single stream if Detach is false.

curl -s --unix-socket /var/run/docker.sock -X POST http://localhost/exec/$ID/start \
    -H "Content-Type: application/json" \
    -d "{\"Detach\": false}" --data-binary "@$FILE"
