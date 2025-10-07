"""
Analyzer module for analyzing backtest results.

This module provides tools for analyzing and visualizing backtest results
stored in CSV files, allowing users to group, filter, and visualize
trading strategy performance across different dimensions.
"""

from typing import Union, List, Optional, Any
from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt

from src.utils.logger import get_logger

logger = get_logger("BacktestAnalyzer")
analysis_result_file_path = 'ORB_config_analysis_summary.csv'

class Analyzer:
    """
    A flexible analyzer for trading strategy backtest results.
    
    This class allows users to analyze backtest results across different
    dimensions, create summary statistics, and visualize relationships
    between metrics.
    """
    
    def __init__(self, results_data: Union[str, pd.DataFrame, Path]):
        """
        Initialize the Analyzer with backtest results.
        
        Args:
            results_data: Either a path to a CSV file, a pandas DataFrame,
                         or a Path object pointing to a CSV file containing
                         backtest results.
        """
        if isinstance(results_data, pd.DataFrame):
            self.data = results_data
        elif isinstance(results_data, (str, Path)):
            self.data = pd.read_csv(results_data)
        else:
            raise TypeError("Results data must be a pandas DataFrame or a path to a CSV file")
    
    def analyze(self, 
               group_by: Union[str, List[str]], 
               metrics: List[str]) -> pd.DataFrame:
        """
        Analyze backtest results by grouping by one or more columns and calculating
        summary statistics for specified metrics.
        
        Args:
            group_by: Column name or list of column names to group results by 
                     (e.g., "ticker" or ["configID", "regime"]).
            metrics: List of metric column names to summarize (e.g., ["expectancy", "sharpe_ratio"]).
        
        Returns:
            DataFrame containing summary statistics (median, std, sum of trades, count) for each metric,
            grouped by the specified column(s). Only includes groups with >= 20 trades.
        
        Raises:
            ValueError: If metrics are missing or group_by columns are not found.
        """
        logger.info(f"Starting analysis with group_by={group_by} and metrics={metrics}")

        # Validate input
        if not metrics:
            logger.error("No metrics provided for analysis")
            raise ValueError("At least one metric must be provided")
        
        # Convert single column to list for consistent handling
        if isinstance(group_by, str):
            group_by = [group_by]
            
        # Validate all group_by columns exist
        for col in group_by:
            if col not in self.data.columns:
                logger.error(f"Group by column '{col}' not found in data")
                raise ValueError(f"Column '{col}' not found in data")
        
        # Validate all metric columns exist
        for metric in metrics:
            if metric not in self.data.columns:
                logger.error(f"Metric column '{metric}' not found in data")
                raise ValueError(f"Metric column '{metric}' not found in data")
        
        # Filter metrics with insufficient trades
        if "num_trades" in self.data.columns:
            original_length = len(self.data)
            self.data = self.data[self.data["num_trades"] >= 50]
            filtered_length = len(self.data)
            logger.info(f"Filtered out {original_length - filtered_length} rows with less than 20 trades")

        logger.info(f"Calculating statistics for metrics: {metrics}")
        analysis_data = self.data.copy()
        grouped = analysis_data.groupby(group_by)
        
        # Calculate median and standard deviation for each metric
        median_df = grouped[metrics].median()
        median_df.columns = [f"{col}_median" for col in median_df.columns]
        
        std_df = grouped[metrics].std()
        std_df.columns = [f"{col}_std" for col in std_df.columns]
        
        # Calculate sum of trades for each group
        if "num_trades" in analysis_data.columns:
            trades_sum_df = grouped["num_trades"].sum().to_frame("total_trades")
            logger.info("Calculated sum of trades for each group")
        else:
            logger.warning("'num_trades' column not found, using group size as trade count")
            trades_sum_df = grouped.size().to_frame("total_trades")
        
        # Combine all statistics into one DataFrame
        summary = pd.concat([median_df, std_df, trades_sum_df], axis=1)
        summary.reset_index(inplace=True)
        
        return summary
    
    def viewScatterPlot(self, x: str, y: str, color: Optional[str] = None) -> None:
        """
        Create a scatter plot visualization of backtest results.
        
        Args:
            x: Column name to plot on the x-axis.
            y: Column name to plot on the y-axis.
            color: Optional column name to use for color-coding points.
                  If provided, a colorbar will be added to the plot.
        
        Raises:
            ValueError: If specified columns are not found in the data.
        """
        # Validate columns
        for col in [x, y]:
            if col not in self.data.columns:
                raise ValueError(f"Column '{col}' not found in data")
        
        if color is not None and color not in self.data.columns:
            raise ValueError(f"Color column '{color}' not found in data")
        
        plt.figure(figsize=(10, 6))
        
        if color:
            scatter = plt.scatter(
                self.data[x],
                self.data[y],
                c=self.data[color],
                cmap='viridis',
                alpha=0.7,
                edgecolors='w'
            )
            plt.colorbar(scatter, label=color)
        else:
            plt.scatter(
                self.data[x],
                self.data[y],
                alpha=0.7,
                edgecolors='w'
            )
        
        plt.xlabel(x)
        plt.ylabel(y)
        title = f"{y} vs {x}"
        if color:
            title += f" (colored by {color})"
        plt.title(title)
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.show()


# Example usage
if __name__ == "__main__":
    results_path = Path(__file__).parent.parent.parent / 'results' / 'backtest' / 'ORB_config_metrics.csv'
    logger.info(f"Loading backtest results from {results_path}")
    
    analyzer = Analyzer(results_path)
    
    # Example 1: Group by single column
    logger.info("Analyzing performance metrics grouped by ticker")
    summary = analyzer.analyze(
        group_by="ticker", 
        metrics=["expectancy", "sharpe_ratio", "win_rate", "max_drawdown", "profit_factor", "average_trade_return", "num_trades"]
    )
    logger.info(f"Analysis complete. Found {len(summary)} tickers with sufficient trades")
    
    # Example 2: Group by multiple columns
    try:
        logger.info("Analyzing performance metrics grouped by configID and regime")
        summary_by_config = analyzer.analyze(
            group_by=["configID", "ticker_regime"], 
            metrics=["expectancy", "sharpe_ratio", "win_rate", "max_drawdown"]
        )
        logger.info(f"Analysis complete. Found {len(summary_by_config)} config-regime combinations with sufficient trades")
        summary_by_config.to_csv(Path(__file__).parent.parent.parent / 'results' / 'backtest' / analysis_result_file_path, index=False)
    except Exception as e:
        logger.error(f"Could not create config summary: {e}")
    
    # Example 3: Visualize relationships
    try:
        logger.info("Creating visualization of expectancy vs max_drawdown")
        analyzer.viewScatterPlot(
            x="max_drawdown", 
            y="expectancy", 
            color="sharpe_ratio"
        )
        logger.info("Visualization complete")
    except Exception as e:
        logger.error(f"Could not create visualization: {e}")