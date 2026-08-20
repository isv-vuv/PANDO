import re


def get_alpha2_code(loc):
    if loc and hasattr(loc, "raw") and loc.raw and "address" in loc.raw and "country_code" in loc.raw["address"]:
        return loc.raw["address"]["country_code"].upper()
    return "XX"


def get_iso3_code(loc, country_provider=None, unavailable="N/A"):
    if country_provider is None:
        return unavailable
    if loc and hasattr(loc, "raw") and loc.raw and "address" in loc.raw and "country_code" in loc.raw["address"]:
        try:
            alpha2 = loc.raw["address"]["country_code"].upper()
            country = country_provider.countries.get(alpha_2=alpha2)
            return country.alpha_3 if country else "UNK"
        except Exception as exc:
            print(f"Error getting ISO3 code: {exc}")
            return "ERR"
    return "NOC"


def get_clean_filename_city(loc, unknown_place="unknown_place"):
    if not (loc and hasattr(loc, "address")):
        return unknown_place

    city = loc.address.split(",")[0]
    replacements = {
        "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
        "Ä": "Ae", "Ö": "Oe", "Ü": "Ue",
        "é": "e", "è": "e", "ê": "e", "ë": "e",
        "É": "E", "È": "E", "Ê": "E", "Ë": "E",
        "á": "a", "à": "a", "â": "a", "ã": "a",
        "Á": "A", "À": "A", "Â": "A", "Ã": "A",
        "í": "i", "ì": "i", "î": "i", "ï": "i",
        "Í": "I", "Ì": "I", "Î": "I", "Ï": "I",
        "ó": "o", "ò": "o", "ô": "o", "õ": "o",
        "Ó": "O", "Ò": "O", "Ô": "O", "Õ": "O",
        "ú": "u", "ù": "u", "û": "u",
        "Ú": "U", "Ù": "U", "Û": "U",
        "ç": "c", "Ç": "C",
        "ñ": "n", "Ñ": "N",
    }
    for old, new in replacements.items():
        city = city.replace(old, new)

    city_clean = re.sub(r"[^\w\s-]", "", city).strip()
    return re.sub(r"[-\s]+", "_", city_clean)

