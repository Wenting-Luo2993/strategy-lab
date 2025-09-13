import mplfinance as mpf

def plot_candlestick(df, indicators=None, title="Candlestick Chart"):
    """
    Plots a candlestick chart with optional indicator overlays.
    
    Args:
        df (pd.DataFrame): DataFrame with ohlcv columns in lowercase.
        indicators (list[str]): List of indicator column names to overlay.
        title (str): Chart title.
    """
    if indicators is None:
        indicators = []

    # Build list of addplots
    addplots = []
    for ind in indicators:
        if ind in df.columns:
            addplots.append(mpf.make_addplot(df[ind], panel=0, ylabel=ind))

    mpf.plot(
        df,
        type="candle",
        style="yahoo",
        addplot=addplots,
        title=title,
        ylabel="Price",
        volume=True,       # Adds volume bars automatically
        figratio=(12, 6),
        figscale=1.2
    )