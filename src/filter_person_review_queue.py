"""Filter the person review queue to plausible two-part personal names.

This does not resolve people or alter the source queue. It selects rows whose
observed span is exactly ``First Last`` or ``m'/M'/s'/S' First Last`` and
rejects curated prose, document-title, institution, commodity, and place
fragments that happen to have two capitalized tokens. When a place authority
file is supplied, exact aliases and strongly geographic name shapes are kept
out of the definite-person output without treating every occurrence of an
ambiguous place word as a place.
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path


NAME_TOKEN = r"[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ0-9'’.-]*"
TITLE_TOKEN = r"(?i:captain|captaine|capt\.?|treasurer|deputie|deputy|sir|lady|lord|chief|king|queen|weroance|doctor|dr\.?|master|mistress|mr\.?|mrs\.?|governor|[mMsS][\'’*]+)"
NON_NAME_INITIALS = {
    "captain", "captaine", "capt", "sir", "lady", "lord", "chief",
    "king", "queen", "weroance", "doctor", "dr", "master", "mistress",
    "mr", "mrs", "miss", "governor", "deputy", "deputie", "treasurer",
    "counsel", "counsell", "councell", "councel",
}
TITLE_NORMALIZATION = {
    "captain": "Captain", "captaine": "Captain", "capt": "Captain", "capt.": "Captain",
    "treasurer": "Treasurer", "deputie": "Deputy", "deputy": "Deputy",
    "sir": "Sir", "lady": "Lady", "lord": "Lord", "chief": "Chief",
    "king": "King", "queen": "Queen", "weroance": "Weroance",
    "doctor": "Doctor", "dr": "Doctor", "dr.": "Doctor",
    "master": "Master", "mistress": "Mistress", "mr": "Mr", "mr.": "Mr",
    "mrs": "Mrs", "mrs.": "Mrs", "governor": "Governor",
}
NON_NAME_LAST_TOKENS = {
    "m", "s", "mr", "sir", "capt", "captain", "captaine", "lady", "lord",
}
NON_NAME_LEADING_TOKENS = {
    "and", "att", "besides", "between", "concerning", "each", "for", "his",
    "in", "my", "of", "one", "the", "to", "touching", "twelve", "vpon",
    "wee", "whereas", "whereupon", "wherevnto", "wheras", "whither",
    "yor", "your",
}
NON_NAME_TRAILING_TOKENS = {
    "all", "and", "between", "by", "for", "in", "of", "the", "to",
    "whereas", "who", "with",
}
NON_NAME_CALENDAR_TOKENS = {
    "april", "february", "january", "june", "july", "march", "october",
    "september",
}

# These tokens describe a geographic feature strongly enough to disqualify a
# two-token PERSON candidate. ``Cape`` is intentionally only a prefix: Cape
# Cod is a place, while Peirce Cape is an attested person in this corpus.
GEOGRAPHIC_FIRST_TOKENS = frozenset({"cape", "county"})
GEOGRAPHIC_LAST_TOKENS = frozenset({
    "bay", "citie", "cittee", "cittic", "cittie", "citty", "cittye",
    "city", "citye", "ciry", "country", "countrie", "county", "creek",
    "creeke", "cytie", "cyttie", "cytty", "cyty", "hund", "hundred",
    "hundreth", "island", "islande", "islandes", "islands", "isle",
    "isles", "plantation", "plantaton", "plantaéon", "point", "port",
    "river", "riuer", "ryver", "sheire", "sheires", "shire", "shires",
    "town", "towne",
})
GEOGRAPHIC_CONTEXT_PREPOSITIONS = frozenset({
    "at", "from", "in", "into", "near", "of", "on", "toward", "towards",
    "to", "upon", "within", "vpon", "vppon",
})

# These are deliberately phrase-level negatives. Individual words such as
# Wrote, Smith, More, May, Land, and King can be personal names in this corpus
# and must not become global hard negatives.
MIXED_PERSON_REVIEW_PHRASES = {
    "adventurers thomas", "alice to", "ancaty four", "arcatt a",
    "arcaty commission", "arcaty proctammations", "argaty lerrer",
    "argall appointment", "argall commission", "argall confirmations",
    "argall procuammations", "argalls guard", "argalls shippe",
    "argals guarde", "argoll and", "arundell and", "bailies demaund",
    "ait john", "barber with", "bargraue the", "bargraues comission",
    "bargraves decree", "barton apothecary", "baylyes proposiéons",
    "berblock and",
    "berckleys plantation", "blaneys magazine", "blevets company",
    "bohun who", "bolton minister", "boys the", "brewsters oath",
    "butler soe", "butlers informaéon", "butlers vnmaskinge", "caswell the",
    "caswells report", "cavendish read", "cavendish s",
    "cauendish earle", "cauendish generall", "cauendish read",
    "cauendish the", "cauendishes proposition",
    "censures wrote", "charles patent", "clarkes account",
    "colliers familie", "collingwood secretary", "concerning martin",
    "conway teste", "copland with", "couentrie knight", "dales crop",
    "dale knight", "darnelly sept", "defends wrote", "deibridue in",
    "desire mt", "dirmers discou",
    "discharge passmore", "each m", "edw committees", "edward land",
    "edward letter", "elizabeth land", "essingtons handé",
    "f errarc house", "ferrar deputy", "ferrar fesruary", "ferrars house",
    "ferrars howse", "ferrars in", "for martins", "for smythes",
    "freeman alderman", "georges departure", "godseall adieu",
    "gookins agent", "gookins ship", "gookins shippe", "hamers letter",
    "harvey marcu", "heath recorder", "humphrey council", "iacobb two",
    "index smith",
    "isaac letter", "isabella land", "isinges all", "jermyn in",
    "jermyns chamber", "iohnson dep", "john to", "johnson and",
    "jotinsons deelaratione", "julius request", "knight lo",
    "knight thier", "lawns ship", "lawrence land", "leeke planter",
    "lorp kasper",
    "martine certificate", "martines burgesses", "martins burgesses",
    "martins certificate",
    "martins demaund", "martins deniall", "martins patent",
    "martins patente", "martins plantation", "mu gen", "norton the",
    "nortons invoice", "peirs account", "pemberton virginia",
    "poreys comission", "pories panegericks", "pory seer", "pray mt",
    "robert committee",
    "robert letter", "roberts addition", "rostingham and",
    "russe us projeect", "sackuile concerninge", "sandys knight",
    "saunds plantaéon", "sheffeild earle", "sheffeld the", "shares newporte",
    "smith accompt", "smiths mocon", "smyth alderm", "smyth of",
    "smyths hand", "somerscales pattent", "southampton the",
    "southamptons account", "spiller said", "staples minister",
    "teste edw", "thorpe of", "thorp deputy", "tracy june",
    "tracys departure", "tucker cap", "tuckers accompt",
    "valentine bookkeeper", "warre governor", "waterhous account",
    "waterhouse petiéon", "waternouse secref", "weldens tobacco",
    "welheloved wiltm", "willet customer", "william letter",
    "william patent", "william petitioner", "wood vppon", "woodalls cattle",
    "wrote c", "wrote departed", "wrote doctor", "wrote gagend", "wrote s",
    "wrotes accuser", "wrotes desire", "wrors notes", "wyarr april",
    "wyatt and", "wyatt between", "wyatt by", "wyatt january",
    "wye milburie", "wyratr between",
}

CLEAR_NON_PERSON_PHRASES = {
    "a lerrer", "aa cet", "accompt ml", "aceustomed zeale",
    "acting beadle", "actions suites",
    "acéons suites", "adventurers associating", "adventurers counsell",
    "adventurers thomas", "aieust sti", "alphabett bookes", "an onder",
    "anno dni", "apomattucke river", "arcaty commission",
    "any tennant", "apprentizes servante", "argall confirmations",
    "argall commission", "argall procuammations", "argalls shippe",
    "armour powder", "ashmolean mss",
    "assignement dated", "attorny generall", "atturney generall",
    "atturny generall", "autograph letter", "autograph letters",
    "autograph signatures", "autograph signed", "bailies demaund",
    "barbarous massacre", "barbarous people", "bargraues comission",
    "bargraves decree", "barlie oates", "bartlett hundred",
    "bee oe", "beet se", "berckleys plantation", "berkeley hundred",
    "bermuda court", "bermuda granary", "bermudaes companye",
    "black box", "ble pon", "blevets company", "bona adventure",
    "bona noua", "bona nous", "bona nova", "booke keeper",
    "borough land", "boughton house", "breife answere", "brewsters oath",
    "brown library",
    "butlers informaéon", "butlers vnmaskinge", "carefull consideraéon",
    "caswells report", "cattle corne", "cattle wee",
    "cauendishes proposition", "certaine articles", "certaine mynes",
    "certaine ouer", "certaine revenew", "certaine rolles",
    "caesar papers", "campanian answere", "certen plantacons",
    "charles hundred", "charles citie", "charles patent",
    "choanocki river", "cir bb", "citie or", "cittie towne",
    "citye towne", "clarkes account", "clear wallnutt", "clb ber",
    "coales millstones", "cofion seale", "coinon seale",
    "colliers familie", "colonies wee", "colony hoggs", "colony sat",
    "colledge land", "colledge lande", "colony seale", "colony serv",
    "colony wee", "come hither", "comion seale", "comission wee",
    "committ ees", "common councell", "common counsell", "common land",
    "common law", "common prayer", "common seall", "common wealthes",
    "comon seale", "companie all", "companies lands", "compimus luculenter",
    "consideration wherof", "consilio jurispitorfii", "cotten woole",
    "councin for", "councmn for", "counsells seale", "counsetls seales",
    "countrye men", "court book", "courte held", "courte itt",
    "covetous disposiéon", "dales crop", "daneing point",
    "dauncing point", "debts due", "deed indented", "defends wrote",
    "deputies account", "deputies leiftennante", "deputy likewise",
    "detained servanté", "directe course", "discharge passmore",
    "doequet book", "dowager countess", "duch shipps", "due return",
    "duty boys", "dy ba",
    "earles lands", "easter eue", "easter term", "easterne shore",
    "earle marshall", "edw committees", "edward demands", "edward land",
    "edward letter", "elisabeth island", "elizabeth citie",
    "elizabeth land", "elizabeth river", "enemyes ged", "england complayning",
    "england scotland", "england seot", "essingtons handé", "excellent ma",
    "excellent mat",
    "ferrar fesruary", "ferrar papers", "ferrars howse", "fower shippes",
    "f errarc house", "ferrars house", "fsquire counsello", "galleved sele",
    "garrison towns",
    "ge wee", "gentlemen cittizens",
    "georges departure", "giving license", "good example", "good newes",
    "good worth", "gookins agent", "gookins ship", "gookins shippe",
    "graceous ma", "gracious letter", "great hopewell", "greate seale",
    "guest houses", "hamers letter", "heath recorder", "high treasurer",
    "high treasuror", "high tresuror", "hogg tland", "holborne bridge",
    "honorable priuie", "huntington library", "husbands executorshippe",
    "iacobb two", "iames cittie", "indyan corne",
    "infidles children", "ioint stocke", "iointe stocke", "ioynt stocke",
    "ireland defender", "ireland defendo", "ireland king", "iron workes",
    "isaac letter", "isabella land", "james citie", "janes town",
    "january anno", "january s", "january ye", "jawfull certificate",
    "jefferson library", "joynt stocke",
    "jotinsons deelaratione", "judicious consideraéon", "kaster term",
    "keepers decree", "kings ships", "knight gouernor", "knight governo",
    "knight governot", "knight lo", "knight thier", "lands cultivated",
    "lawes custome", "lawful debts", "lawfull debts", "lawns ship",
    "lawrence land", "leeke planter", "legall seale",
    "lerned counsell", "letter signed", "liued long", "lo order",
    "lotterie bookes", "lotterie house", "lottery house", "lottrie house",
    "lres patents", "lres pattente", "lres pattents", "magdalene catlege",
    "magdalene college", "magdalene colleze", "magni sigifli",
    "maid answere", "ma counsell", "manchester papers",
    "marriners saylers", "martiall law", "martiall lawe", "martiall lew",
    "martine certificate", "martine hundeed", "martine hundred",
    "martiall courte", "martines burgesses", "martins certificate",
    "martins demaund", "martins deniall", "martins hundred",
    "martins hundreds", "martins patent", "martins patente",
    "martins plantation", "mas counsell", "mat counsell", "mat lettres",
    "mat royall", "matiee courté", "matis court", "mats counsell",
    "maye captin", "mercurii decimo", "merehant tayler", "michaelmas term",
    "michas terme", "mill wrighte", "monakins countrie",
    "mourninge virginia", "nalf stiflncccocccoceeos", "nauigable riuers",
    "neighbouringe salvages", "newporte newes", "newports newes",
    "noble generall", "noble zeale", "noblemen peers", "nores taken",
    "northern seas", "northerne colony adventurors", "nortons invoice",
    "officers chosen", "papers c", "paules churche", "peirs account",
    "pemberton virginia",
    "petitioners complaint", "petizon itt", "plantations chargeing",
    "plantations declaring", "point comforte", "poreys comission",
    "pories panegericks", "preparatiue courte", "president letter",
    "principall secretaryes", "priny counsell",
    "priuey councell", "priuie councell", "priuy councell",
    "priuy counsell", "privie councell", "privie counsell",
    "privy counsell", "propositions wherefore", "pryuie seale",
    "pryvie counsell",
    "pryvy counsell", "psent councell", "publique armorie",
    "publique land", "quarter coort", "quarter counsell",
    "quarter courte", "quarter courts", "queensberry manuscripts",
    "quo warranto", "rent corne", "riuers creeke", "robert committee",
    "robert letter", "rolles contayning",
    "rolles e", "rolles lo", "royall iames", "royall mat",
    "sacred maiesty", "sacred mat", "salaries casheires",
    "salt marishes", "salt pipestaues", "sancte trinitatis", "seale lo",
    "seale whereas", "seals affixed", "seas coastes", "seconp complaint",
    "se letters", "serene tee",
    "setsssisstsitisateiatetisscesesstetsressteereteer sspssesosiseeereres",
    "shares newporte", "sheffeild earle", "ship carpenters",
    "shippe companye", "shippes seamen", "shirley hundreds",
    "siluer oare", "sl pbo", "small barke", "smith accompt",
    "smiths mocon", "smyth alderm", "smyths hand",
    "smyths hundred", "sole importaéon", "somers islanps",
    "somer handé", "somerscales pattent", "sope ashes", "sotherne colony",
    "southamptons account", "southerne colony", "spayne newfoundland",
    "sss ses", "stoke gifford",
    "suffer ilandes", "sufier dande", "sufier dandes", "sumer dland",
    "sumer tlandes", "summer is", "sweete gums", "terra lemnia",
    "teste edw", "the comittees", "the coppie", "the earle", "the ka",
    "the lo", "the second", "ther howses", "thesaurarium socictitis",
    "thier counsell",
    "trading voyages", "treasurer ditferences", "treasurors noble",
    "treasurors proposition", "treatise annexed", "trers allowance",
    "tres patentes", "trinitie term", "tracys departure", "treasorer counsell",
    "treasuro councell", "treasuror counsell", "tréasoror counsell",
    "tucker cap", "tuckers accompt", "tur account", "tur answer",
    "twelve powndé", "tye virginia", "toe certiricate", "toe manner",
    "valentine bookkeeper", "virginea sheweing", "vor wines", "wards lo",
    "wariscoyake martins", "warre governor", "waterhous account",
    "waterhouse petiéon", "waternouse secref", "welbeloved councellors",
    "weldens tobacco", "welheloved wiltm", "wheat barley", "whine af",
    "wie landline", "william letter", "william patent", "william petitioner",
    "winwood papers", "wo pfull counsell", "wood vppon", "worthy friend",
    "worthie comissioners", "wrors notes", "wseut councell", "young maydens",
}

# Additional corpus-specific negatives confirmed from their page context. Keep
# these phrase-level: several component words (for example Rice, King, Wrote,
# and Carpenter) are also legitimate personal names elsewhere in the records.
CLEAR_NON_PERSON_PHRASES.update({
    "agente tenante", "ali grants", "amen audité", "america wee",
    "ancor sayle", "ane artickle", "anglic acetiam", "anglie acctiam",
    "anglie acetiam", "anglie judicem", "anglis locumtenei",
    "arch bishop", "arch bishope", "att whithall", "autograph lettor",
    "axes shouell", "barronett s", "bartholomew lane",
    "beunctts welcome", "bishoprol bathe", "bona speranza",
    "bona venture", "bonny bess", "boston athenaeum", "buerie servant",
    "butt cap",
    "cap bona", "cap generall", "cape bona speranza", "certaine indians",
    "chapoks creek", "choapooks creek", "christian religion",
    "cittie burrough", "collonia london", "comiand cap", "comon wealth",
    "controller lo", "councitin viremia", "counemin virainia",
    "county kent",
    "courte holden", "curie admiraltatis", "curie admit", "d c",
    "decretum quam", "deputy his", "deputy peticone", "dissentinge itt",
    "esquire humbly", "esquire sheweth", "esquire treasuror",
    "esquier clerke", "euerie kingdome", "euerie tenant", "euerye kingdome",
    "fox hill",
    "francis bona", "frauncis bona",
    "fretum hudson", "generall assemblie", "generall courte",
    "generall massacre", "generall tre", "god everlasting", "god king",
    "good mt", "gou nora allow", "graies inn", "grais inn",
    "green cloth", "guesthouse inn", "hemp flax", "hillary term",
    "hillary termes",
    "henrico istand", "hudsons bay", "indian kinge", "indy collonyes",
    "indy companie",
    "infidelle children", "james fort", "kinge ma", "kinge subiecté",
    "knight marshall", "knight s", "l high", "l treasuro",
    "lansdowne mss", "letweene cap", "lincolns inn", "lo keeper",
    "lo treasuror",
    "lo trer", "lorde knight", "lorde presidente", "lordsand knight",
    "lorp presmpent", "lorp treasurer", "low command", "m attorney",
    "m deputy", "m doctor", "m sec", "m secretary", "m treasuror",
    "ma counseil", "ma custome", "ma subiect", "ma third",
    "marshall law", "marshall lawe", "martins hundreth", "monney lent",
    "mulberry iand",
    "n s this", "natiue king", "newberry library", "nowe knowe", "o lo",
    "london printed", "oxford bachelo", "padre maestro", "perticular corpora",
    "planta gon", "planta gons", "plantaéon itt", "plantinge corne",
    "porke baken", "pott ashes", "powder shott", "prblie record",
    "privy councin", "publique vse", "randolph mss", "rave w",
    "regis anglic", "rename kiecowtan", "rex angle", "rice cotten woole",
    "royal james", "royall ma", "sacred ma", "saint michael",
    "salt peter", "sancti michaelis", "sassafras c", "sassafras salt",
    "se vera",
    "sherry sacki", "signett privie", "silk codde", "sithes lane",
    "soe capten", "somers istanps", "sort drarr", "sowthampton hundreth",
    "south my", "spanish pobaeeo", "spanish tobaeeo",
    "starr chamber", "steele iron", "stpremacie allegiance",
    "sumer ilandé", "sunken marsh", "tae kinc", "tame swyne",
    "thomas itt", "threr counsell", "thynne counsell", "tier counsell",
    "treasuror lo", "treasuror this", "tur kine",
    "v vireinia", "virgin colon", "virgin tasconeary", "vnknowne person",
    "wee confesse", "wee receaued", "wile be", "woollen cloth",
    "wyne pitch", "x seals", "yo tis", "yot mat",
})
NAME_PATTERN = re.compile(
    rf"\s*(?:(?P<title>{TITLE_TOKEN})\s+)?"
    rf"(?P<first>{NAME_TOKEN})\s+(?P<last>{NAME_TOKEN})\s*"
)


def normalized_phrase(value: str) -> tuple[str, list[str]]:
    """Return a comparison phrase without changing the observed text."""
    folded = re.sub(
        r"([A-Za-zÀ-ÖØ-öø-ÿ])['’]s\b",
        r"\1s",
        value.casefold(),
    )
    tokens = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]+", folded)
    return " ".join(tokens), tokens


def load_place_aliases(path: Path | None) -> dict[str, set[tuple[str, str, str]]]:
    """Load exact authority aliases without fuzzy place/person matching."""
    forms: dict[str, set[tuple[str, str, str]]] = {}
    if path is None:
        return forms
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            values = [row.get("preferred_name", "")]
            values.extend(row.get("aliases", "").split("|"))
            for value in values:
                phrase, _ = normalized_phrase(value)
                if not phrase:
                    continue
                forms.setdefault(phrase, set()).add((
                    row.get("place_id", ""),
                    row.get("preferred_name", ""),
                    row.get("matching_policy", ""),
                ))
    return forms


def is_strong_geographic_shape(value: str) -> bool:
    """Return true only for explicit geographic constructions."""
    _, tokens = normalized_phrase(value)
    if len(tokens) < 2:
        return False
    return tokens[0] in GEOGRAPHIC_FIRST_TOKENS or tokens[-1] in GEOGRAPHIC_LAST_TOKENS


def has_geographic_sentence_context(value: str, sentence: str) -> bool:
    """Check whether an otherwise ambiguous exact alias is used spatially."""
    value_phrase, value_tokens = normalized_phrase(value)
    sentence_phrase, sentence_tokens = normalized_phrase(sentence)
    if not value_phrase or not sentence_phrase:
        return False
    if is_strong_geographic_shape(value):
        return True
    width = len(value_tokens)
    for index in range(0, len(sentence_tokens) - width + 1):
        if sentence_tokens[index:index + width] != value_tokens:
            continue
        before = sentence_tokens[max(0, index - 3):index]
        after = sentence_tokens[index + width:index + width + 3]
        if any(token in GEOGRAPHIC_CONTEXT_PREPOSITIONS for token in before):
            return True
        if any(token in GEOGRAPHIC_LAST_TOKENS for token in before + after):
            return True
    return False


def place_exclusion_reason(
    value: str,
    sentence: str,
    place_forms: dict[str, set[tuple[str, str, str]]],
) -> str:
    """Return a conservative reason for excluding a PERSON candidate as place."""
    phrase, tokens = normalized_phrase(value)
    hits = place_forms.get(phrase, set())
    if hits:
        policies = {policy for _, _, policy in hits}
        # A multi-token high-confidence alias is explicit enough by itself;
        # single-token aliases still require sentence context because many
        # English place names can also be surnames.
        if "high_confidence_alias" in policies and (
            len(tokens) > 1 or has_geographic_sentence_context(value, sentence)
        ):
            return "exact_high_confidence_place_alias"
        if has_geographic_sentence_context(value, sentence):
            return "contextual_place_alias"
    if is_strong_geographic_shape(value):
        return "explicit_geographic_name_shape"
    return ""


NORMALIZED_MIXED_PERSON_REVIEW_PHRASES = frozenset(
    normalized_phrase(value)[0]
    for value in MIXED_PERSON_REVIEW_PHRASES
    if normalized_phrase(value)[0]
)
NORMALIZED_CLEAR_NON_PERSON_PHRASES = frozenset(
    normalized_phrase(value)[0]
    for value in CLEAR_NON_PERSON_PHRASES
    if normalized_phrase(value)[0]
)


def is_clear_non_person_phrase(value: str) -> bool:
    """Identify high-confidence prose/metadata fragments, never fuzzy names."""
    phrase, tokens = normalized_phrase(value)
    if not tokens:
        return True
    if phrase in NORMALIZED_MIXED_PERSON_REVIEW_PHRASES:
        return False
    if phrase in NORMALIZED_CLEAR_NON_PERSON_PHRASES:
        return True
    if tokens[0] in NON_NAME_LEADING_TOKENS:
        return True
    if tokens[-1] in NON_NAME_TRAILING_TOKENS:
        return True
    if tokens[0] in NON_NAME_CALENDAR_TOKENS or tokens[-1] in NON_NAME_CALENDAR_TOKENS:
        return True
    if tokens[0] == "virginia":
        return True
    return False


def requires_contextual_person_review(value: str) -> bool:
    """Return true for a person token joined to prose, metadata, or a role."""
    phrase, _ = normalized_phrase(value)
    return phrase in NORMALIZED_MIXED_PERSON_REVIEW_PHRASES


def filter_queue(
    source: Path,
    output: Path,
    remainder_output: Path | None = None,
    place_authority: Path | None = None,
) -> int:
    place_forms = load_place_aliases(place_authority)
    with source.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        extra_fields = [
            "matched_title",
            "matched_title_normalized",
            "matched_first_name",
            "matched_last_name",
            "filter_reason",
            "person_pattern_status",
        ]
        output_fields = fieldnames + [field for field in extra_fields if field not in fieldnames]
        matches = []
        remainder = []
        for row in reader:
            span = row.get("observed_span", "")
            candidates = [
                candidate for candidate in NAME_PATTERN.finditer(span)
                if candidate.group("last").casefold().rstrip(".,:;") not in NON_NAME_LAST_TOKENS
                and not is_clear_non_person_phrase(
                    f"{candidate.group('first')} {candidate.group('last')}"
                )
                and not requires_contextual_person_review(
                    f"{candidate.group('first')} {candidate.group('last')}"
                )
                and "\n\n" not in candidate.group(0)
                and (
                    candidate.group("title")
                    or (
                    candidate.group("first").casefold().rstrip(".,:;") not in NON_NAME_INITIALS
                    )
                )
            ]
            match = None
            if candidates:
                # Prefer a title-bearing candidate when it has the same two
                # name tokens as a later untitled candidate. Otherwise use the
                # last valid candidate inside the overlong span.
                for candidate in candidates:
                    if not candidate.group("title"):
                        continue
                    for other in candidates:
                        if (
                            other.group("first").casefold() == candidate.group("first").casefold()
                            and other.group("last").casefold() == candidate.group("last").casefold()
                        ):
                            match = candidate
                            break
                    if match is not None:
                        break
                if match is None:
                    match = candidates[-1]
            if match is None:
                remainder.append(row)
                continue
            title = match.group("title") or ""
            raw_match = match.group(0)
            leading_space = len(raw_match) - len(raw_match.lstrip())
            matched_text = raw_match.strip()
            matched_text = matched_text.rstrip(" .,:;!?)]}")
            candidate_name = f"{match.group('first')} {match.group('last')}"
            place_reason = place_exclusion_reason(
                candidate_name,
                row.get("sentence_text", ""),
                place_forms,
            )
            if place_reason:
                row["filter_reason"] = place_reason
                row["person_pattern_status"] = "excluded_place_candidate"
                remainder.append(row)
                continue
            relative_start = match.start() + leading_space
            row["span_start"] = str(int(row.get("span_start", 0)) + relative_start)
            row["span_end"] = str(int(row["span_start"]) + len(matched_text))
            row["observed_span"] = matched_text
            row["occurrence_id"] = (
                f"{row['page_id']}-M{int(row['span_start']):05d}-"
                f"{int(row['span_end']):05d}"
            )
            row["name_string"] = candidate_name
            row["matched_title"] = title
            title_key = title.casefold()
            if title_key.startswith("m") and title_key not in TITLE_NORMALIZATION:
                normalized_title = "Mr"
            elif title_key.startswith("s") and title_key not in TITLE_NORMALIZATION:
                normalized_title = "Sir"
            else:
                normalized_title = TITLE_NORMALIZATION.get(title_key, title)
            row["matched_title_normalized"] = normalized_title
            row["matched_first_name"] = match.group("first")
            row["matched_last_name"] = match.group("last")
            row["filter_reason"] = (
                "definite_titled_two_part_name" if title else "definite_untitled_two_part_name"
            )
            row["person_pattern_status"] = "definite_person"
            matches.append(row)

    output.parent.mkdir(parents=True, exist_ok=True)
    def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=output_fields)
            writer.writeheader()
            writer.writerows(rows)

    write_rows(output, matches)
    if remainder_output is not None:
        write_rows(remainder_output, remainder)
    return len(matches)


def deduplicate_exact_rows(source: Path, output: Path) -> tuple[int, int]:
    """Collapse only rows that are identical in every CSV column.

    Conflicting rows that share an occurrence ID are deliberately preserved
    for the later reconciliation pass.
    """
    with source.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    seen: set[tuple[str, ...]] = set()
    kept: list[dict[str, str]] = []
    for row in rows:
        signature = tuple(row.get(field, "") for field in fieldnames)
        if signature in seen:
            continue
        seen.add(signature)
        kept.append(row)

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(kept)
    return len(rows), len(kept)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--remainder-output", type=Path)
    parser.add_argument("--place-authority", type=Path)
    parser.add_argument(
        "--deduplicate-exact",
        action="store_true",
        help="Collapse only completely identical rows; preserve conflicting duplicate IDs.",
    )
    args = parser.parse_args()
    if args.deduplicate_exact:
        before, after = deduplicate_exact_rows(args.source, args.output)
        print(f"Kept {after} of {before} rows; removed {before - after} exact duplicates")
    else:
        print(
            f"Exported {filter_queue(args.source, args.output, args.remainder_output, args.place_authority)} "
            "filtered rows"
        )
