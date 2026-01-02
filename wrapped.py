# wrapped.py
import streamlit as st
import pandas as pd
import altair as alt
from typing import Tuple, Optional, Dict


# APP CONFIG

st.set_page_config(page_title="Opay Year in Review", layout="wide")

# Chart colors
GREEN = "#2ECC71"
LIGHT_GREEN = "#A8E6A1"


# HELPER: find header row

def detect_header_row(path: str, sheet_name: str, look_for: str = "Value Date", max_rows: int = 30) -> Optional[int]:
    """
    Read sheet with header=None and scan first `max_rows` rows to find
    a cell that contains `look_for` substring (case-insensitive).
    Returns the row index to use as header, or None if not found.
    """
    preview = pd.read_excel(path, sheet_name=sheet_name, header=None, nrows=max_rows, dtype=str)
    target = look_for.lower()
    for idx, row in preview.iterrows():
        # join row cells and check
        if row.astype(str).str.lower().str.contains(target, na=False).any():
            return idx
    return None


# HELPER: normalize column names to canonical names

def map_columns(cols):
    
    # canonical names we'll use in the app
    required = {
        "value_date": ["value date", "valuedate", "value_date", "value-date", "trans. date", "trans date", "date"],
        "description": ["description", "narration", "particulars", "details", "transaction description"],
        "debit": ["debit", "debit(₦)", "withdrawal", "amount out", "amount"],
        "credit": ["credit", "credit(₦)", "deposit", "amount in"],
        "balance": ["balance after", "balance", "balance after(₦)", "balance(₦)"],
        "channel": ["channel", "channel type", "mode"],
        "reference": ["transaction reference", "reference", "tx ref", "tran ref", "transaction id"]
    }

    # lower-case stripped cols
    lc = [str(c).strip().lower() for c in cols]
    mapping = {}

    for canon, variants in required.items():
        found = None
        for i, c in enumerate(lc):
            for v in variants:
                if v in c:  # substring match
                    found = cols[i]
                    break
            if found:
                break
        if found:
            mapping[canon] = found
        else:
            # Not fatal for optional ones like channel/reference, but for value_date/debit/credit we should flag
            mapping[canon] = None

    # Determine which are missing but required (value_date and debit at minimum)
    missing_required = [k for k in ("value_date", "debit", "credit") if not mapping.get(k)]
    return mapping, missing_required


# DATA LOADING (robust)

def load_data(file, sheet_choice=None) -> Tuple[pd.DataFrame, str]:
    
    # get sheet names
    xls = pd.ExcelFile(file)
    sheets = xls.sheet_names

    # default to first sheet if not provided
    if sheet_choice is None:
        sheet_choice = sheets[0]

    # detect header row
    header_row = detect_header_row(file, sheet_choice, look_for="Value Date", max_rows=40)
    if header_row is None:
        # fallback: try a few common header terms
        header_row = detect_header_row(file, sheet_choice, look_for="Trans. Date", max_rows=40)
    if header_row is None:
        # If still None - read with header=0 and hope for best
        df = pd.read_excel(file, sheet_name=sheet_choice, header=0)
    else:
        # Use detected header row
        df = pd.read_excel(file, sheet_name=sheet_choice, header=header_row)

    # Strip column names
    df.columns = [str(c).strip() for c in df.columns]
    return df, sheet_choice

# PREPROCESSING (normalize + parse)

def preprocess_data(df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str,str]]:
    """
    Normalize columns, rename to canonical names, parse dates and amounts.
    Returns cleaned dataframe and mapping from canonical -> actual column name used.
    Raises ValueError with helpful message when essential columns are missing.
    """
    orig_cols = list(df.columns)
    mapping, missing_required = map_columns(orig_cols)

    if missing_required:
        # helpful error message
        raise ValueError(
            f"Required columns not found in the sheet header: {missing_required}. "
            "Make sure your sheet contains 'Value Date', 'Debit' and 'Credit' (or similar). "
            f"Detected columns: {orig_cols[:20]}"
        )

    # Rename columns to canonical keys for use inside app
    rename_map = {}
    for canon, colname in mapping.items():
        if colname:
            rename_map[colname] = canon

    df = df.rename(columns=rename_map)

    # Parse value_date
    df["value_date"] = pd.to_datetime(df["value_date"], errors="coerce")

    # Convert amounts: remove currency symbols, commas, parentheses (for negative), then to float
    def parse_amount(col):
        s = df[col].astype(str).str.replace(r'[^\d\-\.\,()]', '', regex=True)
        # handle parentheses as negative
        s = s.str.replace(r'^\((.*)\)$', r'-\1', regex=True)
        s = s.str.replace(',', '', regex=True)
        return pd.to_numeric(s, errors="coerce").fillna(0.0)

    # If debit/credit columns are named 'debit'/'credit' (canon keys), parse
    for key in ("debit", "credit", "balance"):
        if key in df.columns:
            df[key] = parse_amount(key)

    # Make sure we have a Description column (if not, create from remaining text)
    if "description" not in df.columns:
        # try to combine any text-like column
        text_cols = [c for c in df.columns if df[c].dtype == object]
        if text_cols:
            df["description"] = df[text_cols[0]].astype(str)
        else:
            df["description"] = ""

    # Fill NaNs in optional columns
    for opt in ("channel", "reference"):
        if opt not in df.columns:
            df[opt] = ""

    # Create Month and Weekday helper cols
    df["Month"] = df["value_date"].dt.to_period("M").astype(str)
    df["Weekday"] = df["value_date"].dt.day_name()

    return df, mapping


# KPI COMPUTATION & RENDER

def compute_kpis(df: pd.DataFrame) -> dict:
    total_spend = df["debit"].sum()
    total_income = df["credit"].sum()
    net_cashflow = total_income - total_spend
    avg_daily_spend = df.groupby("value_date")["debit"].sum().mean()
    lowest_balance = df["balance"].replace(0, pd.NA).min() if "balance" in df.columns else pd.NA
    top_channel = df["channel"].mode().iloc[0] if df["channel"].notna().any() else "Unknown"

    return {
        "total_spend": total_spend,
        "total_income": total_income,
        "net_cashflow": net_cashflow,
        "avg_daily_spend": avg_daily_spend,
        "lowest_balance": lowest_balance,
        "top_channel": top_channel
    }

def render_kpis(kpis: dict):
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("💸 Total Spend", f"₦{kpis['total_spend']:,.2f}")
    c2.metric("💰 Total Income", f"₦{kpis['total_income']:,.2f}")
    c3.metric("📊 Net Cashflow", f"₦{kpis['net_cashflow']:,.2f}")
    c4.metric("📅 Avg Daily Spend", f"₦{kpis['avg_daily_spend']:,.2f}")
    low_bal = "N/A" if pd.isna(kpis["lowest_balance"]) else f"₦{kpis['lowest_balance']:,.2f}"
    c5.metric("⚠️ Lowest Balance", low_bal)
    c6.metric("🔁 Top Channel", kpis["top_channel"])


# CATEGORIZATION FUNCTIONS

def categorize_spending(description: str) -> str:
    """
    Categorize spending based on description patterns.
    Returns a category label.
    """
    desc_lower = description.lower()
    
    # Define category patterns (order matters for priority)
    patterns = {
        "Airtime": ["airtime", "recharge", "top up", "topup", "mtn", "glo", "9mobile", "etisalat"],
        "Transfers": ["transfer", "send", "pay", "debit", "withdrawal"],
        "Bills & Utilities": ["utility", "electric", "power", "water", "internet", "subscription"],
        "Betting": ["bet", "stake", "gaming", "nairabet", "bet9ja", "sportybet"],
        "POS & Withdrawals": ["pos", "withdrawal", "atm", "cash"],
        "Food & Dining": ["restaurant", "food", "pizza", "chicken", "fuel", "gas"],
        "Shopping": ["shop", "store", "buy", "purchase", "mall"],
        "Entertainment": ["cinema", "movie", "ticket", "concert", "show"],
        "Education": ["school", "tuition", "course", "training", "lesson"],
    }
    
    for category, keywords in patterns.items():
        for keyword in keywords:
            if keyword in desc_lower:
                return category
    
    return "Other"

def categorize_income(description: str) -> str:
    """
    Categorize income sources based on description patterns.
    Returns a category label.
    """
    desc_lower = description.lower()
    
    patterns = {
        "Salary": ["salary", "payment", "wage", "monthly"],
        "Transfers": ["transfer", "send", "received", "from"],
        "Refunds": ["refund", "reversal", "credit back", "adjustment"],
        "Cashback": ["cashback", "reward", "bonus", "promo"],
        "Interest": ["interest", "dividend", "earnings"],
    }
    
    for category, keywords in patterns.items():
        for keyword in keywords:
            if keyword in desc_lower:
                return category
    
    return "Other"

# CHARTS & INSIGHTS

def render_charts(df: pd.DataFrame):
    # Aggregations
    daily_spend = df.groupby("value_date")["debit"].sum().sort_index()
    monthly = df.groupby("Month")[["debit", "credit"]].sum()
    channel_spend = df.groupby("channel")["debit"].sum().sort_values(ascending=False)
    weekday_spend = df.groupby("Weekday")["debit"].sum().reindex(
        ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    ).fillna(0.0)
    
    # Categorize spending and income
    df["spending_category"] = df[df["debit"] > 0]["description"].apply(categorize_spending)
    df["income_category"] = df[df["credit"] > 0]["description"].apply(categorize_income)
    
    spending_by_category = df[df["debit"] > 0].groupby("spending_category")["debit"].sum().sort_values(ascending=False)
    income_by_category = df[df["credit"] > 0].groupby("income_category")["credit"].sum().sort_values(ascending=False)
    
    # Row 1: Daily Spending Trend & Monthly Comparison
    col1, col2 = st.columns([2, 1])
    with col1:
        st.markdown("<h3 style='text-align:center'>Daily Spending Trend</h3>", unsafe_allow_html=True)
        df_daily = daily_spend.reset_index(name='amount')
        chart_daily = alt.Chart(df_daily).mark_line(color=GREEN).encode(
            x=alt.X('value_date:T', title='Date'),
            y=alt.Y('amount:Q', title='Daily Spend')
        ).properties(width='container', height=250)
        st.altair_chart(chart_daily, use_container_width=True)
    with col2:
        st.markdown("<h3 style='text-align:center'>Monthly Income vs Spending</h3>", unsafe_allow_html=True)
        df_monthly = monthly.reset_index().melt(id_vars=['Month'], value_vars=['debit', 'credit'], var_name='type', value_name='amount')
        chart_monthly = alt.Chart(df_monthly).mark_bar().encode(
            x=alt.X('Month:N', sort=None, title='Month'),
            y=alt.Y('amount:Q', title='Amount', axis=None),
            color=alt.Color('type:N', scale=alt.Scale(domain=['debit', 'credit'], range=[GREEN, LIGHT_GREEN]), legend=alt.Legend(title='Type'))
        ).properties(width='container', height=250)
        st.altair_chart(chart_monthly, use_container_width=True)

    st.divider()

    # Row 2: Spending by Category, Spending by Weekday & Transaction Volume
    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown("<h3 style='text-align:center'>Spending by Category</h3>", unsafe_allow_html=True)
        df_spending_cat = spending_by_category.reset_index(name='amount')
        chart_spending_cat = alt.Chart(df_spending_cat).mark_arc(innerRadius=50).encode(
            theta=alt.Theta('amount:Q'),
            color=alt.Color('spending_category:N', scale=alt.Scale(scheme='greens')),
            tooltip=['spending_category:N', alt.Tooltip('amount:Q', format=',.2f')]
        ).properties(width=300, height=300)
        st.altair_chart(chart_spending_cat, use_container_width=True)
    with col2:
        st.markdown("<h3 style='text-align:center'>Spending by Weekday</h3>", unsafe_allow_html=True)
        df_weekday = weekday_spend.reset_index(name='amount').rename(columns={"Weekday": "weekday"})
        bars = alt.Chart(df_weekday).mark_bar(color=GREEN).encode(
            x=alt.X('weekday:N', sort=['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'], title='Weekday'),
            y=alt.Y('amount:Q', axis=None)
        )
        text = bars.mark_text(align='center', baseline='bottom').encode(
            y=alt.Y('amount:Q'),
            text=alt.Text('amount:Q', format=',.0f')
        )
        chart_weekday = (bars + text).properties(width='container', height=250)
        st.altair_chart(chart_weekday, use_container_width=True)
    with col3:
        st.markdown("<h3 style='text-align:center'>Transaction Volume by Month</h3>", unsafe_allow_html=True)
        txn_volume = df.groupby("Month").size()
        df_txn = txn_volume.reset_index(name='count')
        bars_txn = alt.Chart(df_txn).mark_bar(color=GREEN).encode(
            x=alt.X('Month:N', sort=None, title='Month'),
            y=alt.Y('count:Q', axis=None)
        )
        text_txn = bars_txn.mark_text(align='center', baseline='bottom').encode(
            y=alt.Y('count:Q'),
            text=alt.Text('count:Q', format=',.0f')
        )
        chart_txn = (bars_txn + text_txn).properties(width='container', height=250)
        st.altair_chart(chart_txn, use_container_width=True)

    st.divider()

    # Top descriptions and highest spending days in same row
    top_desc = df[df["debit"] > 0].groupby("description")["debit"].sum().sort_values(ascending=False).head(5)
    spike_days = daily_spend.sort_values(ascending=False).head(8)

    top_credit_sources = df[df["credit"] > 0].groupby("description")["credit"].sum().sort_values(ascending=False).head(5)

    col_top, col_spike, col_credit = st.columns(3)
    with col_top:
        st.markdown("<h3 style='text-align:center'>Top Transfer Descriptions</h3>", unsafe_allow_html=True)
        st.table(top_desc)
    with col_spike:
        st.markdown("<h3 style='text-align:center'>Highest Spending Days</h3>", unsafe_allow_html=True)
        st.table(spike_days)
    with col_credit:
        st.markdown("<h3 style='text-align:center'>Top Credit Sources</h3>", unsafe_allow_html=True)
        st.table(top_credit_sources)




# MAIN

def main():

    col1, col2, col3 = st.columns([1, 0.2, 1])
    with col2:
        st.image("logo.png", use_container_width=True)
    st.markdown("<h1 style='text-align:center'>Opay Yearly Wrapped up</h1>", unsafe_allow_html=True)

    uploaded_file = st.file_uploader("Upload your bank statement (Excel, .xlsx)", type=["xlsx"])
    if not uploaded_file:
        st.info("Upload an Excel workbook with your transactions. The app will try to auto-detect the header row.")
        return

    # Preview sheet names
    xls = pd.ExcelFile(uploaded_file)
    sheets = xls.sheet_names
    chosen_sheet = st.selectbox("Select sheet to analyze", sheets, index=0)

    # load and preprocess
    df_raw, used_sheet = load_data(uploaded_file, sheet_choice=chosen_sheet)

    # show small preview to user and let them confirm the header detection
    st.markdown("<h3>Preview (first 8 rows) — from sheet: {}</h3>".format(used_sheet), unsafe_allow_html=True)
    st.dataframe(df_raw.head(8), use_container_width=True)

    st.markdown("<h1 style='text-align:center'>📊 SPENDING OVERVIEW</h1>", unsafe_allow_html=True)
    st.write("")

    # preprocess & normalize; surface errors to user
    try:
        df, mapping = preprocess_data(df_raw)
    except Exception as e:
        st.error(str(e))
        return

    # show mapped columns so user knows which columns got used
    # st.caption(f"Column mapping (canonical -> actual): {mapping}")

    # compute & render KPIs
    kpis = compute_kpis(df)
    render_kpis(kpis)

    st.divider()
    # charts
    render_charts(df)

if __name__ == "__main__":
    main()
