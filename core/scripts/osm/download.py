import os
import requests

GEOFABRIK_MAP = {
    "ALB": "europe/albania-latest.osm.pbf",
    "AND": "europe/andorra-latest.osm.pbf",
    "DEU": "europe/germany-latest.osm.pbf",
    "CHE": "europe/switzerland-latest.osm.pbf",
    "FRA": "europe/france-latest.osm.pbf"
}


def download_pbf_files(iso_codes, osm_dir):
    print("\n--- Module: Downloading osm.pbf files started ---")
    downloaded_files = []

    for code in iso_codes:
        code_upper = code.upper().strip()
        if code_upper not in GEOFABRIK_MAP:
            print(f"ISO code '{code_upper}' is currently not supported or unknown.")
            continue

        sub_path = GEOFABRIK_MAP[code_upper]
        url = f"https://download.geofabrik.de/{sub_path}"
        filename = os.path.basename(sub_path)
        dest_path = os.path.join(osm_dir, filename)

        print(f"Checking/Downloading {filename} from Geofabrik...")
        if os.path.exists(dest_path):
            print(f"-> File already exists locally: {dest_path}")
            downloaded_files.append(dest_path)
            continue

        response = requests.get(url, stream=True)
        if response.status_code == 200:
            with open(dest_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
            print(f"-> Successfully downloaded: {dest_path}")
            downloaded_files.append(dest_path)
        else:
            print(f"Download failed for {code_upper}: Status code {response.status_code}")

    return downloaded_files