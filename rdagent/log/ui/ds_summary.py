import math
import pickle
import re
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from streamlit import session_state as state

from rdagent.log.ui.conf import UI_SETTING
from rdagent.log.ui.ds_trace import load_times
from rdagent.scenarios.kaggle.kaggle_crawler import leaderboard_scores


def get_metric_direction(competition: str):
    leaderboard = leaderboard_scores(competition)
    return float(leaderboard[0]) > float(leaderboard[-1])


def get_script_time(stdout_p: Path):
    with stdout_p.open("r") as f:
        first_line = next(f).strip()
        last_line = deque(f, maxlen=1).pop().strip()

        # Extract timestamps from the lines
        first_time_match = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\+\d{2}:\d{2})", first_line)
        last_time_match = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\+\d{2}:\d{2})", last_line)

        if first_time_match and last_time_match:
            first_time = datetime.fromisoformat(first_time_match.group(1))
            last_time = datetime.fromisoformat(last_time_match.group(1))
            return pd.Timedelta(last_time - first_time)

    return None


def get_final_sota_exp(log_path: Path):
    sota_exp_paths = [i for i in log_path.rglob(f"**/SOTA experiment/**/*.pkl")]
    if len(sota_exp_paths) == 0:
        return None
    final_sota_exp_path = max(sota_exp_paths, key=lambda x: int(re.match(r".*Loop_(\d+).*", str(x))[1]))
    with final_sota_exp_path.open("rb") as f:
        final_sota_exp = pickle.load(f)
    return final_sota_exp


# @st.cache_data(persist=True)
def get_summary_df(log_folders: list[str]) -> tuple[dict, pd.DataFrame]:
    summarys = {}
    with st.sidebar:
        if st.toggle("show 24h summary", key="show_hours_summary"):
            sn = "summary_24h.pkl"
        else:
            sn = "summary.pkl"
    for lf in log_folders:
        if not (Path(lf) / sn).exists():
            st.warning(
                f"{sn} not found in **{lf}**\n\nRun:`dotenv run -- python rdagent/log/mle_summary.py grade_summary --log_folder={lf} --hours=<>`"
            )
        else:
            summarys[lf] = pd.read_pickle(Path(lf) / sn)

    if len(summarys) == 0:
        return {}, pd.DataFrame()

    summary = {}
    for lf, s in summarys.items():
        for k, v in s.items():
            stdout_p = Path(lf) / f"{k}.stdout"
            if stdout_p.exists():
                v["script_time"] = get_script_time(stdout_p)
            else:
                v["script_time"] = None

            exp_gen_time = timedelta()
            coding_time = timedelta()
            running_time = timedelta()
            all_time = timedelta()
            times_info = load_times(Path(lf) / k)
            for time_info in times_info.values():
                all_time += sum((ti.end - ti.start for ti in time_info), timedelta())
                exp_gen_time += time_info[0].end - time_info[0].start
                if len(time_info) > 1:
                    coding_time += time_info[1].end - time_info[1].start
                if len(time_info) > 2:
                    running_time += time_info[2].end - time_info[2].start
            v["exec_time"] = str(all_time).split(".")[0]
            v["exp_gen_time"] = str(exp_gen_time).split(".")[0]
            v["coding_time"] = str(coding_time).split(".")[0]
            v["running_time"] = str(running_time).split(".")[0]

            final_sota_exp = get_final_sota_exp(Path(lf) / k)
            if final_sota_exp is not None and final_sota_exp.result is not None:
                v["sota_exp_score_valid"] = final_sota_exp.result.loc["ensemble"].iloc[0]
            else:
                v["sota_exp_score_valid"] = None
            # 调整实验名字
            if "amlt" in lf:
                summary[f"{lf[lf.rfind('amlt')+5:].split('/')[0]} - {k}"] = v
            elif "ep" in lf:
                summary[f"{lf[lf.rfind('ep'):]} - {k}"] = v
            else:
                summary[f"{lf} - {k}"] = v

    summary = {k: v for k, v in summary.items() if "competition" in v}
    base_df = pd.DataFrame(
        columns=[
            "Competition",
            "Script Time",
            "Exec Time",
            "Exp Gen",
            "Coding",
            "Running",
            "Total Loops",
            "Successful Final Decision",
            "Made Submission",
            "Valid Submission",
            "V/M",
            "Above Median",
            "Bronze",
            "Silver",
            "Gold",
            "Any Medal",
            "Best Result",
            "SOTA Exp",
            "SOTA Exp Score (valid)",
            "SOTA Exp Score",
            "Baseline Score",
            "Ours - Base",
            "Ours vs Base",
            "Ours vs Bronze",
            "Ours vs Silver",
            "Ours vs Gold",
            "Bronze Threshold",
            "Silver Threshold",
            "Gold Threshold",
            "Medium Threshold",
        ],
        index=summary.keys(),
    )

    # Read baseline results
    baseline_result_path = UI_SETTING.baseline_result_path
    if Path(baseline_result_path).exists():
        baseline_df = pd.read_csv(baseline_result_path)

    def compare_score(s1, s2):
        if s1 is None or s2 is None:
            return None
        try:
            c_value = math.exp(abs(math.log(s1 / s2)))
        except Exception as e:
            c_value = None
        return c_value

    for k, v in summary.items():
        loop_num = v["loop_num"]
        base_df.loc[k, "Competition"] = v["competition"]
        base_df.loc[k, "Script Time"] = v["script_time"]
        base_df.loc[k, "Exec Time"] = v["exec_time"]
        base_df.loc[k, "Exp Gen"] = v["exp_gen_time"]
        base_df.loc[k, "Coding"] = v["coding_time"]
        base_df.loc[k, "Running"] = v["running_time"]
        base_df.loc[k, "Total Loops"] = loop_num
        if loop_num == 0:
            base_df.loc[k] = "N/A"
        else:
            base_df.loc[k, "Successful Final Decision"] = v["success_loop_num"]
            base_df.loc[k, "Made Submission"] = v["made_submission_num"]
            if v["made_submission_num"] > 0:
                base_df.loc[k, "Best Result"] = "made_submission"
            base_df.loc[k, "Valid Submission"] = v["valid_submission_num"]
            if v["valid_submission_num"] > 0:
                base_df.loc[k, "Best Result"] = "valid_submission"
            base_df.loc[k, "Above Median"] = v["above_median_num"]
            if v["above_median_num"] > 0:
                base_df.loc[k, "Best Result"] = "above_median"
            base_df.loc[k, "Bronze"] = v["bronze_num"]
            if v["bronze_num"] > 0:
                base_df.loc[k, "Best Result"] = "bronze"
            base_df.loc[k, "Silver"] = v["silver_num"]
            if v["silver_num"] > 0:
                base_df.loc[k, "Best Result"] = "silver"
            base_df.loc[k, "Gold"] = v["gold_num"]
            if v["gold_num"] > 0:
                base_df.loc[k, "Best Result"] = "gold"
            base_df.loc[k, "Any Medal"] = v["get_medal_num"]

            baseline_score = None
            if Path(baseline_result_path).exists():
                baseline_score = baseline_df.loc[baseline_df["competition_id"] == v["competition"], "score"].item()

            base_df.loc[k, "SOTA Exp"] = v.get("sota_exp_stat", None)
            if baseline_score is not None and v.get("sota_exp_score", None) is not None:
                base_df.loc[k, "Ours - Base"] = v["sota_exp_score"] - baseline_score
            base_df.loc[k, "Ours vs Base"] = compare_score(v["sota_exp_score"], baseline_score)
            base_df.loc[k, "Ours vs Bronze"] = compare_score(v["sota_exp_score"], v.get("bronze_threshold", None))
            base_df.loc[k, "Ours vs Silver"] = compare_score(v["sota_exp_score"], v.get("silver_threshold", None))
            base_df.loc[k, "Ours vs Gold"] = compare_score(v["sota_exp_score"], v.get("gold_threshold", None))
            base_df.loc[k, "SOTA Exp Score"] = v.get("sota_exp_score", None)
            base_df.loc[k, "SOTA Exp Score (valid)"] = v.get("sota_exp_score_valid", None)
            base_df.loc[k, "Baseline Score"] = baseline_score
            base_df.loc[k, "Bronze Threshold"] = v.get("bronze_threshold", None)
            base_df.loc[k, "Silver Threshold"] = v.get("silver_threshold", None)
            base_df.loc[k, "Gold Threshold"] = v.get("gold_threshold", None)
            base_df.loc[k, "Medium Threshold"] = v.get("median_threshold", None)

    base_df["SOTA Exp"] = base_df["SOTA Exp"].replace("", pd.NA)
    base_df = base_df.astype(
        {
            "Total Loops": int,
            "Successful Final Decision": int,
            "Made Submission": int,
            "Valid Submission": int,
            "Above Median": int,
            "Bronze": int,
            "Silver": int,
            "Gold": int,
            "Any Medal": int,
            "Ours - Base": float,
            "Ours vs Base": float,
            "SOTA Exp Score": float,
            "SOTA Exp Score (valid)": float,
            "Baseline Score": float,
            "Bronze Threshold": float,
            "Silver Threshold": float,
            "Gold Threshold": float,
            "Medium Threshold": float,
        }
    )
    return summary, base_df


def num2percent(num: int, total: int, show_origin=True) -> str:
    num = int(num)
    total = int(total)
    if show_origin:
        return f"{num} ({round(num / total * 100, 2)}%)"
    return f"{round(num / total * 100, 2)}%"


def percent_df(df: pd.DataFrame, show_origin=True) -> pd.DataFrame:
    base_df = df.copy(deep=True)

    # Convert columns to object dtype so we can store strings like "14 (53.85%)" without warnings
    columns_to_convert = [
        "Successful Final Decision",
        "Made Submission",
        "Valid Submission",
        "Above Median",
        "Bronze",
        "Silver",
        "Gold",
        "Any Medal",
    ]
    base_df[columns_to_convert] = base_df[columns_to_convert].astype(object)

    for k in base_df.index:
        loop_num = int(base_df.loc[k, "Total Loops"])
        if loop_num != 0:
            base_df.loc[k, "Successful Final Decision"] = num2percent(
                base_df.loc[k, "Successful Final Decision"], loop_num, show_origin
            )
            if base_df.loc[k, "Made Submission"] != 0:
                base_df.loc[k, "V/M"] = (
                    f"{round(base_df.loc[k, 'Valid Submission'] / base_df.loc[k, 'Made Submission'] * 100, 2)}%"
                )
            else:
                base_df.loc[k, "V/M"] = "N/A"
            base_df.loc[k, "Made Submission"] = num2percent(base_df.loc[k, "Made Submission"], loop_num, show_origin)
            base_df.loc[k, "Valid Submission"] = num2percent(base_df.loc[k, "Valid Submission"], loop_num, show_origin)
            base_df.loc[k, "Above Median"] = num2percent(base_df.loc[k, "Above Median"], loop_num, show_origin)
            base_df.loc[k, "Bronze"] = num2percent(base_df.loc[k, "Bronze"], loop_num, show_origin)
            base_df.loc[k, "Silver"] = num2percent(base_df.loc[k, "Silver"], loop_num, show_origin)
            base_df.loc[k, "Gold"] = num2percent(base_df.loc[k, "Gold"], loop_num, show_origin)
            base_df.loc[k, "Any Medal"] = num2percent(base_df.loc[k, "Any Medal"], loop_num, show_origin)

    return base_df


def days_summarize_win():
    lfs1 = [re.sub(r"log\.srv\d*", "log.srv", folder) for folder in state.log_folders]
    lfs2 = [re.sub(r"log\.srv\d*", "log.srv2", folder) for folder in state.log_folders]
    lfs3 = [re.sub(r"log\.srv\d*", "log.srv3", folder) for folder in state.log_folders]

    _, df1 = get_summary_df(lfs1)
    _, df2 = get_summary_df(lfs2)
    _, df3 = get_summary_df(lfs3)

    df = pd.concat([df1, df2, df3], axis=0)

    def mean_func(x: pd.DataFrame):
        numeric_cols = x.select_dtypes(include=["int", "float"]).mean()
        string_cols = x.select_dtypes(include=["object"]).agg(lambda col: ", ".join(col.fillna("none").astype(str)))
        return pd.concat([numeric_cols, string_cols], axis=0).reindex(x.columns).drop("Competition")

    df = df.groupby("Competition").apply(mean_func)
    if st.toggle("Show Percent", key="show_percent"):
        st.dataframe(percent_df(df, show_origin=False))
    else:
        st.dataframe(df)


LITE = [
    "aerial-cactus-identification",
    "aptos2019-blindness-detection",
    "denoising-dirty-documents",
    "detecting-insults-in-social-commentary",
    "dog-breed-identification",
    "dogs-vs-cats-redux-kernels-edition",
    "histopathologic-cancer-detection",
    "jigsaw-toxic-comment-classification-challenge",
    "leaf-classification",
    "mlsp-2013-birds",
    "new-york-city-taxi-fare-prediction",
    "nomad2018-predict-transparent-conductors",
    "plant-pathology-2020-fgvc7",
    "random-acts-of-pizza",
    "ranzcr-clip-catheter-line-classification",
    "siim-isic-melanoma-classification",
    "spooky-author-identification",
    "tabular-playground-series-dec-2021",
    "tabular-playground-series-may-2022",
    "text-normalization-challenge-english-language",
    "text-normalization-challenge-russian-language",
    "the-icml-2013-whale-challenge-right-whale-redux",
]


def all_summarize_win():
    def shorten_folder_name(folder: str) -> str:
        if "amlt" in folder:
            return folder[folder.rfind("amlt") + 5 :].split("/")[0]
        if "ep" in folder:
            return folder[folder.rfind("ep") :]
        return folder

    selected_folders = st.multiselect(
        "Show these folders", state.log_folders, state.log_folders, format_func=shorten_folder_name
    )
    summary, base_df = get_summary_df(selected_folders)
    if not summary:
        return

    base_df = percent_df(base_df)
    base_df.insert(0, "Select", True)
    bt1, bt2 = st.columns(2)
    if bt2.toggle("Select Lite Competitions", key="select_lite"):
        base_df["Select"] = base_df["Competition"].apply(lambda x: x in LITE)
    else:
        base_df["Select"] = True

    if bt1.toggle("Select Best", key="select_best"):

        def apply_func(cdf: pd.DataFrame):
            cp = cdf["Competition"].values[0]
            md = get_metric_direction(cp)
            if md:
                best_idx = cdf["SOTA Exp Score (valid)"].idxmax()
            else:
                best_idx = cdf["SOTA Exp Score (valid)"].idxmin()
            return best_idx

        best_idxs = base_df.groupby("Competition").apply(apply_func)
        base_df["Select"] = base_df.index.isin(best_idxs.values)
    else:
        base_df["Select"] = True

    base_df = st.data_editor(
        base_df.style.apply(
            lambda col: col.map(lambda val: "background-color: #F0F8FF"),
            subset=["Baseline Score", "Bronze Threshold", "Silver Threshold", "Gold Threshold", "Medium Threshold"],
            axis=0,
        )
        .apply(
            lambda col: col.map(lambda val: "background-color: #FFFFE0"),
            subset=[
                "Ours - Base",
                "Ours vs Base",
                "Ours vs Bronze",
                "Ours vs Silver",
                "Ours vs Gold",
            ],
            axis=0,
        )
        .apply(
            lambda col: col.map(lambda val: "background-color: #E6E6FA"),
            subset=[
                "Script Time",
                "Exec Time",
                "Exp Gen",
                "Coding",
                "Running",
            ],
            axis=0,
        )
        .apply(
            lambda col: col.map(lambda val: "background-color: #F0FFF0"),
            subset=[
                "Best Result",
                "SOTA Exp",
                "SOTA Exp Score",
                "SOTA Exp Score (valid)",
            ],
            axis=0,
        ),
        column_config={
            "Select": st.column_config.CheckboxColumn("Select", default=True, help="Stat this trace.", disabled=False),
        },
        disabled=(col for col in base_df.columns if col not in ["Select"]),
    )
    st.markdown("Ours vs Base: `math.exp(abs(math.log(sota_exp_score / baseline_score)))`")

    # 统计选择的比赛
    base_df = base_df[base_df["Select"]]
    st.markdown(f"**统计的比赛数目: :red[{base_df.shape[0]}]**")
    total_stat = (
        base_df[
            [
                "Made Submission",
                "Valid Submission",
                "Above Median",
                "Bronze",
                "Silver",
                "Gold",
                "Any Medal",
            ]
        ]
        != "0 (0.0%)"
    ).sum()
    total_stat.name = "总体统计(%)"
    total_stat.loc["Bronze"] = base_df["Best Result"].value_counts().get("bronze", 0)
    total_stat.loc["Silver"] = base_df["Best Result"].value_counts().get("silver", 0)
    total_stat.loc["Gold"] = base_df["Best Result"].value_counts().get("gold", 0)
    total_stat = total_stat / base_df.shape[0] * 100

    # SOTA Exp 统计
    se_counts = base_df["SOTA Exp"].value_counts(dropna=True)
    se_counts.loc["made_submission"] = se_counts.sum()
    se_counts.loc["Any Medal"] = se_counts.get("gold", 0) + se_counts.get("silver", 0) + se_counts.get("bronze", 0)
    se_counts.loc["above_median"] = se_counts.get("above_median", 0) + se_counts.get("Any Medal", 0)
    se_counts.loc["valid_submission"] = se_counts.get("valid_submission", 0) + se_counts.get("above_median", 0)

    sota_exp_stat = pd.Series(index=total_stat.index, dtype=int, name="SOTA Exp 统计(%)")
    sota_exp_stat.loc["Made Submission"] = se_counts.get("made_submission", 0)
    sota_exp_stat.loc["Valid Submission"] = se_counts.get("valid_submission", 0)
    sota_exp_stat.loc["Above Median"] = se_counts.get("above_median", 0)
    sota_exp_stat.loc["Bronze"] = se_counts.get("bronze", 0)
    sota_exp_stat.loc["Silver"] = se_counts.get("silver", 0)
    sota_exp_stat.loc["Gold"] = se_counts.get("gold", 0)
    sota_exp_stat.loc["Any Medal"] = se_counts.get("Any Medal", 0)
    sota_exp_stat = sota_exp_stat / base_df.shape[0] * 100

    stat_df = pd.concat([total_stat, sota_exp_stat], axis=1)
    stat_t0, stat_t1 = st.columns(2)
    with stat_t0:
        st.dataframe(stat_df.round(2))
        markdown_table = f"""
| xxx | {stat_df.iloc[0,1]:.1f} | {stat_df.iloc[1,1]:.1f} | {stat_df.iloc[2,1]:.1f} | {stat_df.iloc[3,1]:.1f} | {stat_df.iloc[4,1]:.1f} | {stat_df.iloc[5,1]:.1f} | {stat_df.iloc[6,1]:.1f}   |
"""
        st.text(markdown_table)
    with stat_t1:
        Loop_counts = base_df["Total Loops"]
        fig = px.histogram(Loop_counts, nbins=10, title="Total Loops Histogram (nbins=10)")
        mean_value = Loop_counts.mean()
        median_value = Loop_counts.median()
        fig.add_vline(
            x=mean_value, line_color="orange", annotation_text="Mean", annotation_position="top right", line_width=3
        )
        fig.add_vline(
            x=median_value, line_color="red", annotation_text="Median", annotation_position="top right", line_width=3
        )
        st.plotly_chart(fig)

    # write curve
    st.subheader("Curves", divider="rainbow")
    if st.toggle("Show Curves", key="show_curves"):
        for k, v in summary.items():
            with st.container(border=True):
                st.markdown(f"**:blue[{k}] - :violet[{v['competition']}]**")
                try:
                    tscores = {f"loop {k-1}": v for k, v in v["test_scores"].items()}
                    vscores = {}
                    for k, vs in v["valid_scores"].items():
                        if not vs.index.is_unique:
                            st.warning(
                                f"Loop {k}'s valid scores index are not unique, only the last one will be kept to show."
                            )
                            st.write(vs)
                        vscores[k] = vs[~vs.index.duplicated(keep="last")].iloc[:, 0]

                    if len(vscores) > 0:
                        metric_name = list(vscores.values())[0].name
                    else:
                        metric_name = "None"

                    tdf = pd.Series(tscores, name="score")
                    vdf = pd.DataFrame(vscores)
                    if "ensemble" in vdf.index:
                        ensemble_row = vdf.loc[["ensemble"]]
                        vdf = pd.concat([ensemble_row, vdf.drop("ensemble")])
                    vdf.columns = [f"loop {i}" for i in vdf.columns]
                    fig = go.Figure()
                    # Add test scores trace from tdf
                    fig.add_trace(
                        go.Scatter(
                            x=tdf.index,
                            y=tdf,
                            mode="lines+markers",
                            name="Test scores",
                            marker=dict(symbol="diamond"),
                            line=dict(shape="linear", dash="dash"),
                        )
                    )
                    # Add valid score traces from vdf (transposed to have loops on x-axis)
                    for column in vdf.T.columns:
                        fig.add_trace(
                            go.Scatter(
                                x=vdf.T.index,
                                y=vdf.T[column],
                                mode="lines+markers",
                                name=f"{column}",
                                visible=("legendonly" if column != "ensemble" else None),
                            )
                        )
                    fig.update_layout(title=f"Test and Valid scores (metric: {metric_name})")

                    st.plotly_chart(fig)
                except Exception as e:
                    import traceback

                    st.markdown("- Error: " + str(e))
                    st.code(traceback.format_exc())
                    st.markdown("- Valid Scores: ")
                    # st.write({k: type(v) for k, v in v["valid_scores"].items()})
                    st.json(v["valid_scores"])


with st.container(border=True):
    if st.toggle("近3天平均", key="show_3days"):
        days_summarize_win()
with st.container(border=True):
    all_summarize_win()
