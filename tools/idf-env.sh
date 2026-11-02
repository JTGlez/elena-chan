# Source this file to activate ESP-IDF v6.1 in bash or zsh.
# ESP-IDF keeps its own Python environment, separate from the server's.

: "${ESP_IDF_DIR:=$HOME/esp/esp-idf-v6.1}"

# ESP-IDF's export script takes the first python3 on PATH, so select 3.12 here.
export PATH="$HOME/esp/bin:$PATH"
. "$ESP_IDF_DIR/export.sh"
