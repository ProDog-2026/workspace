#!/bin/bash
root=$(CDPATH= cd -P -- "$(dirname "$0")" && pwd -P) || exit 1
exec /bin/bash "$root/stop.sh"
