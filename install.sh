#!/bin/sh
# Install MusePost and its standard-library modules together. Credentials and
# message state stay in the user's config/state directories, outside this tree.
set -eu

if [ "$#" -gt 1 ]; then
    printf 'Usage: %s [prefix]\n' "$0" >&2
    exit 2
fi

source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
prefix=${1:-"$HOME/.local"}
case "$prefix" in
    /*) ;;
    *) prefix="$(pwd)/$prefix" ;;
esac
python_command=${MUSEPOST_PYTHON:-python3}
"$python_command" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else "MusePost requires Python 3.9 or newer")'

# Check the whole package before replacing any installed files.
for file in musepost musepost_mail.py musepost_config.py musepost_state.py VERSION; do
    if [ ! -f "$source_dir/$file" ]; then
        printf 'Missing package file: %s\n' "$file" >&2
        exit 1
    fi
done

install -d -m 755 "$prefix/lib/musepost" "$prefix/bin"
install -m 755 "$source_dir/musepost" "$prefix/lib/musepost/musepost"
for file in musepost_mail.py musepost_config.py musepost_state.py VERSION; do
    install -m 644 "$source_dir/$file" "$prefix/lib/musepost/$file"
done

# Resolve the sibling library from the launcher, including prefixes with spaces.
# MUSEPOST_PYTHON may select a particular installed interpreter at runtime.
cat > "$prefix/bin/musepost" <<'EOF'
#!/bin/sh
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "${MUSEPOST_PYTHON:-python3}" "$script_dir/../lib/musepost/musepost" "$@"
EOF
chmod 755 "$prefix/bin/musepost"

printf 'Installed MusePost %s to %s\n' "$(cat "$source_dir/VERSION")" "$prefix/bin/musepost"
printf 'Add %s/bin to PATH if needed. Run musepost --help to check the installation.\n' "$prefix"
