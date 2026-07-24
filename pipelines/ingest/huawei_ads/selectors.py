"""华为 ADS 车型页选择器常量。"""

SOURCE_URL = "https://auto.huawei.com/cn/vehicle-models"

TOTAL_COUNT_REGEX = r"共计有\s*(\d+)\s*款车型展示"

LIST_CONTAINER_SELECTOR = "#data-container"
CARD_SELECTOR = "#data-container > .cars-list"
IMAGE_SELECTOR = ".cars-list-pic picture img, .cars-list-pic img"
TITLE_SELECTOR = ".dec-title h3"
SUBTITLE_SELECTOR = ".dec-title h4"
DETAIL_LINK_SELECTOR = "a.cars-more.ga-car-partner, a.cars-more"
SOLUTIONS_LABEL_SELECTOR = ".dec-list-con > h5"
SOLUTION_GROUP_SELECTOR = ".dec-list-item"
SOLUTION_GROUP_NAME_SELECTOR = "h6"
SOLUTION_FEATURE_SELECTOR = ".item-sec .item span"
EXTRA_TIP_SELECTOR = ".dec-list-con .tip .dec, .dec-list-con .tip"
MORE_SECTION_SELECTOR = ".cars-list-more"
