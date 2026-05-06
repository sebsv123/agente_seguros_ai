import subprocess, gzip, tarfile, io, json, os

# Extract the blob as raw binary
result = subprocess.run(['git', 'cat-file', '-p', 'ea5c503df96925573d2ad4313cff805baf4e625f'], capture_output=True)
raw_data = result.stdout

# Decompress gzip
decompressed = gzip.decompress(raw_data)

# Create output directory
os.makedirs('n8n_extracted', exist_ok=True)

# Open as tar
tf = tarfile.open(fileobj=io.BytesIO(decompressed))
print('=== Contents of n8n_data_backup.tgz ===')
for m in tf.getmembers():
    print(f'  {m.name} ({m.size} bytes)')
    if m.isfile():
        f = tf.extractfile(m)
        if f:
            content = f.read()
            # Save to output directory
            out_path = os.path.join('n8n_extracted', m.name.lstrip('./'))
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, 'wb') as out:
                out.write(content)
            if m.name.endswith('.json'):
                try:
                    parsed = json.loads(content)
                    print(f'    -> Valid JSON! Keys: {list(parsed.keys())[:10] if isinstance(parsed, dict) else f"list of {len(parsed)}"}')
                except:
                    print(f'    -> Not JSON')
            elif m.name.endswith('.sqlite') or '.db' in m.name:
                print(f'    -> Database file ({len(content)} bytes)')

print('\n=== Extraction complete! ===')
print(f'Files extracted to: n8n_extracted/')
