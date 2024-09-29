# %%
import json
import subprocess
import time
import zipfile
from itertools import chain
from pathlib import Path

import nbformat
from jinja2 import Environment, StrictUndefined
from rich import print
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By

from rdagent.app.kaggle.conf import KAGGLE_IMPLEMENT_SETTING
from rdagent.core.prompts import Prompts
from rdagent.log import rdagent_logger as logger
from rdagent.oai.llm_utils import APIBackend

# %%
options = webdriver.ChromeOptions()
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.add_argument("--headless")

service = Service("/usr/local/bin/chromedriver")


def crawl_descriptions(competition: str, wait: float = 3.0, force: bool = False) -> dict[str, str]:
    if (fp := Path(f"{KAGGLE_IMPLEMENT_SETTING.local_data_path}/{competition}.json")).exists() and not force:
        logger.info(f"Found {competition}.json, loading from local file.")
        with fp.open("r") as f:
            return json.load(f)

    driver = webdriver.Chrome(options=options, service=service)
    overview_url = f"https://www.kaggle.com/competitions/{competition}/overview"
    driver.get(overview_url)
    time.sleep(wait)
    site_body = driver.find_element(By.ID, "site-content")
    descriptions = {}

    # Get the subtitles
    elements = site_body.find_elements(By.CSS_SELECTOR, f"a[href^='/competitions/{competition}/overview/']")
    subtitles = []
    for e in elements:
        inner_text = ""
        for child in e.find_elements(By.XPATH, ".//*"):
            inner_text += child.get_attribute("innerHTML").strip()
        subtitles.append(inner_text)

    # Get main contents
    contents = []
    elements = site_body.find_elements(By.CSS_SELECTOR, ".fbHzUd")
    for e in elements:
        content = e.get_attribute("innerHTML")
        contents.append(content)

    assert len(subtitles) == len(contents) + 1 and subtitles[-1] == "Citation"
    for i in range(len(subtitles) - 1):
        descriptions[subtitles[i]] = contents[i]

    # Get the citation
    element = site_body.find_element(By.CSS_SELECTOR, ".bZEXEC")
    citation = element.get_attribute("innerHTML")
    descriptions[subtitles[-1]] = citation

    data_url = f"https://www.kaggle.com/competitions/{competition}/data"
    driver.get(data_url)
    time.sleep(wait)
    data_element = driver.find_element(By.CSS_SELECTOR, ".fbHzUd")
    descriptions["Data Description"] = data_element.get_attribute("innerHTML")

    driver.quit()
    with open(f"{KAGGLE_IMPLEMENT_SETTING.local_data_path}/{competition}.json", "w") as f:
        json.dump(descriptions, f)
    return descriptions


def download_data(competition: str, local_path: str = "/data/userdata/share/kaggle") -> None:
    data_path = f"{local_path}/{competition}"
    if not Path(data_path).exists():
        subprocess.run(["kaggle", "competitions", "download", "-c", competition, "-p", data_path])

        # unzip data
        with zipfile.ZipFile(f"{data_path}/{competition}.zip", "r") as zip_ref:
            zip_ref.extractall(data_path)


def download_notebooks(
    competition: str, local_path: str = "/data/userdata/share/kaggle/notebooks", num: int = 15
) -> None:
    data_path = Path(f"{local_path}/{competition}")
    from kaggle.api.kaggle_api_extended import KaggleApi

    api = KaggleApi()
    api.authenticate()

    # judge the sort_by
    ll = api.competition_leaderboard_view(competition)
    score_diff = float(ll[0].score) - float(ll[-1].score)
    if score_diff > 0:
        sort_by = "scoreDescending"
    else:
        sort_by = "scoreAscending"

    # download notebooks
    nl = api.kernels_list(competition=competition, sort_by=sort_by, page=1, page_size=num)
    for nb in nl:
        author = nb.ref.split("/")[0]
        api.kernels_pull(nb.ref, path=data_path / author)
    print(f"Downloaded {len(nl)} notebooks for {competition}. ([red]{sort_by}[/red])")


def notebook_to_knowledge(notebook_text: str) -> str:
    prompt_dict = Prompts(file_path=Path(__file__).parent / "prompts.yaml")

    sys_prompt = (
        Environment(undefined=StrictUndefined)
        .from_string(prompt_dict["gen_knowledge_from_code_mini_case"]["system"])
        .render()
    )

    user_prompt = (
        Environment(undefined=StrictUndefined)
        .from_string(prompt_dict["gen_knowledge_from_code_mini_case"]["user"])
        .render(notebook=notebook_text)
    )

    response = APIBackend().build_messages_and_create_chat_completion(
        user_prompt=user_prompt,
        system_prompt=sys_prompt,
        json_mode=False,
    )
    return response


def convert_notebooks_to_text(competition: str, local_path: str = "/data/userdata/share/kaggle/notebooks") -> None:
    data_path = Path(f"{local_path}/{competition}")
    converted_num = 0

    # convert ipynb and irnb files
    for nb_path in chain(data_path.glob("**/*.ipynb"), data_path.glob("**/*.irnb")):
        with nb_path.open("r", encoding="utf-8") as f:
            nb = nbformat.read(f, as_version=4)
        text = []
        for cell in nb.cells:
            if cell.cell_type == "markdown":
                text.append(f"```markdown\n{cell.source}```")
            elif cell.cell_type == "code":
                text.append(f"```code\n{cell.source}```")
        text = "\n\n".join(text)

        text = notebook_to_knowledge(text)

        text_path = nb_path.with_suffix(".txt")
        text_path.write_text(text, encoding="utf-8")
        converted_num += 1

    # convert py files
    for py_path in data_path.glob("**/*.py"):
        with py_path.open("r", encoding="utf-8") as f:
            text = f"```code\n{f.read()}```"

        text = notebook_to_knowledge(text)

        text_path = py_path.with_suffix(".txt")
        text_path.write_text(text, encoding="utf-8")
        converted_num += 1

    print(f"Converted {converted_num} notebooks to text files.")


def collect_knowledge_texts(local_path: str = "/data/userdata/share/kaggle") -> dict[str, list[str]]:
    """
    {
        "competition1": [
            "knowledge_text1",
            "knowledge_text2",
            ...
        ],
        “competition2”: [
            "knowledge_text1",
            "knowledge_text2",
            ...
        ],
        ...
    }
    """
    notebooks_dir = Path(local_path) / "notebooks"

    competition_knowledge_texts_dict = {}
    for competition_dir in notebooks_dir.iterdir():
        knowledge_texts = []
        for text_path in competition_dir.glob("**/*.txt"):
            text = text_path.read_text(encoding="utf-8")
            knowledge_texts.append(text)

        competition_knowledge_texts_dict[competition_dir.name] = knowledge_texts

    return competition_knowledge_texts_dict


# %%
if __name__ == "__main__":
    mini_case_cs = [
        "feedback-prize-english-language-learning",
        "playground-series-s3e11",
        "playground-series-s3e14",
        "spaceship-titanic",
        "playground-series-s3e18",
        "playground-series-s3e16",
        "playground-series-s3e9",
        "playground-series-s3e25",
        "playground-series-s3e26",
        "playground-series-s3e24",
        "playground-series-s3e23",
    ]

    other_cs = [
        "amp-parkinsons-disease-progression-prediction",
        "arc-prize-2024",
        "ariel-data-challenge-2024",
        "child-mind-institute-detect-sleep-states",
        "connectx",
        "contradictory-my-dear-watson",
        "digit-recognizer",
        "fathomnet-out-of-sample-detection",
        "forest-cover-type-prediction",
        "gan-getting-started",
        "google-research-identify-contrails-reduce-global-warming",
        "house-prices-advanced-regression-techniques",
        "isic-2024-challenge",
        "leash-BELKA",
        "llm-20-questions",
        "nlp-getting-started",
        "playground-series-s4e1",
        "playground-series-s4e2",
        "playground-series-s4e3",
        "playground-series-s4e4",
        "playground-series-s4e5",
        "playground-series-s4e6",
        "playground-series-s4e7",
        "playground-series-s4e8",
        "rsna-2024-lumbar-spine-degenerative-classification",
        "sf-crime",
        "store-sales-time-series-forecasting",
        "titanic",
        "tpu-getting-started",
        # scenario competition
        "covid19-global-forecasting-week-1",
        "statoil-iceberg-classifier-challenge",
        "optiver-realized-volatility-prediction",
        "facebook-v-predicting-check-ins",
    ]

    all_cs = mini_case_cs + other_cs
    for c in all_cs:
        convert_notebooks_to_text(c)
    exit()
    from kaggle.api.kaggle_api_extended import KaggleApi

    api = KaggleApi()
    api.authenticate()
    cs = api.competitions_list()
    for c in cs:
        name = c.ref.split("/")[-1]
        crawl_descriptions(name)
