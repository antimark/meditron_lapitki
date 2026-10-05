from dict import FIELDS


# Поля, где 1 / 0 означают наличие / отсутствие признака
BOOLEAN_FIELDS = {
    "art_hyper",
    "atr_fibril",
    "copd",
    "dm",
    "tlt",
    "ecg_elevation",
}


# Специальные кодированные значения
VALUE_MAPS = {

    "mi_localisation": {
        "A": "передняя",
        "I": "нижняя",
        "L": "боковая",
        "N": "не уточнена",
    },

    "type_acs": {
        "STEMI": "ОКС с подъёмом сегмента ST",
        "NSTEMI": "ОКС без подъёма сегмента ST",
        "NA": "тип ОКС не установлен",
    },

    "ca_fact": {
        "Y": "коронарография выполнена",
        "R": "отказ от коронарографии",
        "N": "данных нет",
    },

    "ca_lad": {
        "0": "стеноз менее 50%",
        "1": "стеноз 50–89%",
        "2": "стеноз 90% и более или окклюзия",
    },

    "rca": {
        "0": "стеноз менее 50%",
        "1": "стеноз 50–89%",
        "2": "стеноз 90% и более или окклюзия",
    },
}


def make_short_label(description):
    """
    Из полного пояснения словаря делает короткое
    название для интерфейса.

    Например:

    "Тип ОКС: STEMI — с подъёмом ST..."
    ->
    "Тип ОКС"
    """

    positions = []

    for separator in [":", ","]:
        position = description.find(separator)

        if position != -1:
            positions.append(position)

    if positions:
        description = description[:min(positions)]

    return description.strip()


def decode_value(key, value):

    if value is None:
        return "не указано"

    value_string = str(value)

    if value_string.lower() == "не указано":
        return "не указано"

    # 1 / 0
    if key in BOOLEAN_FIELDS:

        if value_string == "1":
            return "да"

        if value_string == "0":
            return "нет"

    # Специальные кодировки
    if key in VALUE_MAPS:

        return VALUE_MAPS[key].get(
            value_string,
            value_string
        )

    # Killip
    if key == "killip" and value_string.isdigit():
        return f"{value_string} класс"

    return value_string


def build_history(json_data):

    result = []

    # Важно: ничего не сортируем.
    # Порядок остаётся таким же, как в JSON.

    for section_name, section_data in json_data.items():

        section = {
            "title": section_name[:1].upper() + section_name[1:],
            "fields": []
        }

        if not isinstance(section_data, dict):
            continue

        for key, raw_value in section_data.items():

            description = FIELDS.get(
                key,
                key
            )

            field = {
                "key": key,

                "path": f"{section_name}.{key}",

                "label": make_short_label(
                    description
                ),

                "description": description,

                "raw_value": raw_value,

                "value": decode_value(
                    key,
                    raw_value
                ),
            }

            section["fields"].append(field)

        result.append(section)

    return result