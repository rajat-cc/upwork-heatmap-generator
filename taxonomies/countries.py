"""Country normalisation.

The API returns client countries in several spellings ("United States",
"USA", "GBR", "UK"). Analysis groups by ISO 3166-1 alpha-2 so the #1 market
is one row, not two. Unknown spellings pass through unchanged (and are logged
once) so nothing is silently lost.
"""

from __future__ import annotations

from core.logging_setup import get_logger

log = get_logger(__name__)

# alpha-2 → display name
NAMES: dict[str, str] = {
    "US": "United States", "GB": "United Kingdom", "CA": "Canada", "AU": "Australia",
    "IN": "India", "AE": "United Arab Emirates", "PK": "Pakistan", "NG": "Nigeria",
    "DE": "Germany", "FR": "France", "NL": "Netherlands", "SG": "Singapore",
    "IL": "Israel", "ES": "Spain", "IT": "Italy", "CH": "Switzerland", "SE": "Sweden",
    "NO": "Norway", "DK": "Denmark", "FI": "Finland", "IE": "Ireland", "BE": "Belgium",
    "AT": "Austria", "PL": "Poland", "PT": "Portugal", "CZ": "Czechia", "RO": "Romania",
    "UA": "Ukraine", "TR": "Türkiye", "SA": "Saudi Arabia", "QA": "Qatar", "KW": "Kuwait",
    "EG": "Egypt", "ZA": "South Africa", "KE": "Kenya", "GH": "Ghana", "MA": "Morocco",
    "BR": "Brazil", "MX": "Mexico", "AR": "Argentina", "CL": "Chile", "CO": "Colombia",
    "PE": "Peru", "JP": "Japan", "KR": "South Korea", "CN": "China", "HK": "Hong Kong",
    "TW": "Taiwan", "PH": "Philippines", "ID": "Indonesia", "MY": "Malaysia",
    "TH": "Thailand", "VN": "Vietnam", "BD": "Bangladesh", "LK": "Sri Lanka", "NP": "Nepal",
    "NZ": "New Zealand", "RU": "Russia", "GR": "Greece", "HU": "Hungary", "BG": "Bulgaria",
    "RS": "Serbia", "HR": "Croatia", "LT": "Lithuania", "LV": "Latvia", "EE": "Estonia",
    "CY": "Cyprus", "MT": "Malta", "LU": "Luxembourg", "JO": "Jordan", "LB": "Lebanon",
    "BH": "Bahrain", "OM": "Oman", "GE": "Georgia", "AM": "Armenia", "KZ": "Kazakhstan",
    "MU": "Mauritius", "TZ": "Tanzania", "UG": "Uganda", "ET": "Ethiopia", "CR": "Costa Rica",
    "PA": "Panama", "DO": "Dominican Republic", "PR": "Puerto Rico", "JM": "Jamaica",
    "TT": "Trinidad and Tobago", "BS": "Bahamas", "IS": "Iceland", "SK": "Slovakia",
    "SI": "Slovenia", "MK": "North Macedonia", "BA": "Bosnia and Herzegovina",
    "AL": "Albania", "MD": "Moldova", "BY": "Belarus", "UY": "Uruguay", "EC": "Ecuador",
    "GT": "Guatemala", "SV": "El Salvador", "HN": "Honduras", "NI": "Nicaragua",
    "BO": "Bolivia", "PY": "Paraguay", "VE": "Venezuela", "DZ": "Algeria", "TN": "Tunisia",
    "SN": "Senegal", "CI": "Ivory Coast", "CM": "Cameroon", "RW": "Rwanda", "ZW": "Zimbabwe",
    "ZM": "Zambia", "MW": "Malawi", "MZ": "Mozambique", "AO": "Angola", "NA": "Namibia",
    "BW": "Botswana", "IQ": "Iraq", "IR": "Iran", "AF": "Afghanistan", "UZ": "Uzbekistan",
    "KG": "Kyrgyzstan", "AZ": "Azerbaijan", "MN": "Mongolia", "KH": "Cambodia", "LA": "Laos",
    "MM": "Myanmar", "BN": "Brunei", "MV": "Maldives", "BT": "Bhutan", "FJ": "Fiji",
}  # fmt: skip

# alpha-3 → alpha-2 (the API occasionally returns three-letter codes)
ALPHA3: dict[str, str] = {
    "USA": "US", "GBR": "GB", "CAN": "CA", "AUS": "AU", "IND": "IN", "ARE": "AE", "PAK": "PK",
    "NGA": "NG", "DEU": "DE", "FRA": "FR", "NLD": "NL", "SGP": "SG", "ISR": "IL", "ESP": "ES",
    "ITA": "IT", "CHE": "CH", "SWE": "SE", "NOR": "NO", "DNK": "DK", "FIN": "FI", "IRL": "IE",
    "BEL": "BE", "AUT": "AT", "POL": "PL", "PRT": "PT", "CZE": "CZ", "ROU": "RO", "UKR": "UA",
    "TUR": "TR", "SAU": "SA", "QAT": "QA", "KWT": "KW", "EGY": "EG", "ZAF": "ZA", "KEN": "KE",
    "GHA": "GH", "MAR": "MA", "BRA": "BR", "MEX": "MX", "ARG": "AR", "CHL": "CL", "COL": "CO",
    "PER": "PE", "JPN": "JP", "KOR": "KR", "CHN": "CN", "HKG": "HK", "TWN": "TW", "PHL": "PH",
    "IDN": "ID", "MYS": "MY", "THA": "TH", "VNM": "VN", "BGD": "BD", "LKA": "LK", "NPL": "NP",
    "NZL": "NZ", "RUS": "RU", "GRC": "GR", "HUN": "HU", "BGR": "BG", "SRB": "RS", "HRV": "HR",
    "LTU": "LT", "LVA": "LV", "EST": "EE", "CYP": "CY", "MLT": "MT", "LUX": "LU", "JOR": "JO",
    "LBN": "LB", "BHR": "BH", "OMN": "OM", "GEO": "GE", "ARM": "AM", "KAZ": "KZ", "MUS": "MU",
    "TZA": "TZ", "UGA": "UG", "ETH": "ET", "CRI": "CR", "PAN": "PA", "DOM": "DO", "PRI": "PR",
    "JAM": "JM", "TTO": "TT", "BHS": "BS", "ISL": "IS", "SVK": "SK", "SVN": "SI", "MKD": "MK",
    "BIH": "BA", "ALB": "AL", "MDA": "MD", "BLR": "BY", "URY": "UY", "ECU": "EC", "GTM": "GT",
    "SLV": "SV", "HND": "HN", "NIC": "NI", "BOL": "BO", "PRY": "PY", "VEN": "VE", "DZA": "DZ",
    "TUN": "TN", "SEN": "SN", "CIV": "CI", "CMR": "CM", "RWA": "RW", "ZWE": "ZW", "ZMB": "ZM",
    "MWI": "MW", "MOZ": "MZ", "AGO": "AO", "NAM": "NA", "BWA": "BW", "IRQ": "IQ", "IRN": "IR",
    "AFG": "AF", "UZB": "UZ", "KGZ": "KG", "AZE": "AZ", "MNG": "MN", "KHM": "KH", "LAO": "LA",
    "MMR": "MM", "BRN": "BN", "MDV": "MV", "BTN": "BT", "FJI": "FJ",
}  # fmt: skip

# lower-cased spellings beyond the canonical display names
ALIASES: dict[str, str] = {
    "usa": "US", "u.s.": "US", "u.s.a.": "US", "united states of america": "US",
    "america": "US", "uk": "GB", "u.k.": "GB", "great britain": "GB", "britain": "GB",
    "england": "GB", "scotland": "GB", "wales": "GB", "northern ireland": "GB",
    "uae": "AE", "u.a.e.": "AE", "emirates": "AE", "korea": "KR", "republic of korea": "KR",
    "korea, republic of": "KR", "turkey": "TR", "turkiye": "TR", "czech republic": "CZ",
    "russian federation": "RU", "viet nam": "VN", "hong kong sar": "HK", "macedonia": "MK",
    "the netherlands": "NL", "holland": "NL", "cote d'ivoire": "CI", "côte d'ivoire": "CI",
    "iran, islamic republic of": "IR", "lao people's democratic republic": "LA",
    "brunei darussalam": "BN", "moldova, republic of": "MD", "tanzania, united republic of": "TZ",
    "bolivia, plurinational state of": "BO", "venezuela, bolivarian republic of": "VE",
    "taiwan, province of china": "TW", "syrian arab republic": "SY",
}  # fmt: skip

_BY_NAME = {name.lower(): code for code, name in NAMES.items()}
_warned: set[str] = set()


def to_iso2(value: str | None) -> str:
    """Return the alpha-2 code for any spelling we know; unknown values pass through."""
    raw = (value or "").strip()
    if not raw:
        return ""
    key = raw.lower()
    if key in ALIASES:
        return ALIASES[key]
    if key in _BY_NAME:
        return _BY_NAME[key]
    upper = raw.upper()
    if len(upper) == 2 and upper in NAMES:
        return upper
    if len(upper) == 3 and upper in ALPHA3:
        return ALPHA3[upper]
    if raw not in _warned:
        _warned.add(raw)
        log.info("Unknown country spelling kept as-is: %r", raw)
    return raw


def iso2_to_name(code: str | None) -> str:
    """Display name for an alpha-2 code; anything else is returned unchanged."""
    c = (code or "").strip()
    return NAMES.get(c.upper(), c)
