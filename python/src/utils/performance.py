"""
Performance tracking module for measuring execution time and resource usage.

This module provides tools for tracking performance metrics across different
components of the backtesting system, helping identify bottlenecks and
opportunities for optimization.
"""

import time
import functools
import contextlib
from typing import Dict, List, Any, Optional, Callable
import threading
import logging
from dataclasses import dataclass, field

# Get logger for this module
logger = logging.getLogger(__name__)

@dataclass
class PerformanceMetric:
    """Class to store performance metrics for an operation."""
    name: str
    context: Dict[str, Any] = field(default_factory=dict)
    parent: Optional['PerformanceMetric'] = None
    start_time: float = 0
    end_time: float = 0
    children: List['PerformanceMetric'] = field(default_factory=list)
    
    @property
    def duration(self) -> float:
        """Calculate duration in seconds."""
        return self.end_time - self.start_time
    
    def add_child(self, child: 'PerformanceMetric') -> None:
        """Add a child operation to this metric."""
        self.children.append(child)
        child.parent = self
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format."""
        return {
            'name': self.name,
            'context': self.context,
            'duration': self.duration,
            'children': [child.to_dict() for child in self.children]
        }
        
    def summary(self, indent=0) -> str:
        """Generate human-readable summary."""
        result = f"{' ' * indent}{self.name}: {self.duration:.4f}s"
        if self.context:
            context_str = ", ".join(f"{k}={v}" for k, v in self.context.items())
            result += f" ({context_str})"
        return result

class PerformanceTracker:
    """Centralized performance tracking system for measuring execution time."""
    
    _instance = None
    _lock = threading.Lock()
    
    @classmethod
    def get_instance(cls):
        """Get the singleton instance of PerformanceTracker."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = PerformanceTracker()
        return cls._instance
    
    def __init__(self):
        """Initialize the performance tracker."""
        self.root_metric = PerformanceMetric("root")
        self.current_metric = self.root_metric
        self.enabled = True
        self._metrics_stack = []
        
    def start_tracking(self, name: str, context: Dict[str, Any] = None) -> 'PerformanceMetric':
        """Start tracking a new operation."""
        if not self.enabled:
            return None
            
        metric = PerformanceMetric(
            name=name,
            context=context or {},
            parent=self.current_metric,
            start_time=time.time()
        )
        
        self.current_metric.add_child(metric)
        self._metrics_stack.append(self.current_metric)
        self.current_metric = metric
        return metric
        
    def stop_tracking(self, metric: Optional['PerformanceMetric'] = None) -> None:
        """Stop tracking the current operation."""
        if not self.enabled or not self._metrics_stack:
            return
            
        current = self.current_metric
        current.end_time = time.time()
        
        if self._metrics_stack:
            self.current_metric = self._metrics_stack.pop()
        else:
            self.current_metric = self.root_metric
            
        return current
    
    def generate_report(self, include_root=False) -> str:
        """Generate a hierarchical report of all performance metrics."""
        lines = ["Performance Report:"]
        
        def _print_metric(metric, indent=0):
            if metric.end_time > 0:  # Only report completed metrics
                lines.append(metric.summary(indent))
                for child in sorted(metric.children, key=lambda x: x.duration, reverse=True):
                    _print_metric(child, indent + 2)
        
        if include_root:
            _print_metric(self.root_metric)
        else:
            for child in sorted(self.root_metric.children, key=lambda x: x.duration, reverse=True):
                _print_metric(child)
            
        return "\n".join(lines)
    
    def get_slowest_operations(self, top_n=10) -> List[PerformanceMetric]:
        """Return the slowest n operations."""
        all_metrics = []
        
        def collect_metrics(metric):
            if metric.end_time > 0 and metric.name != "root":
                all_metrics.append(metric)
                for child in metric.children:
                    collect_metrics(child)
                    
        for child in self.root_metric.children:
            collect_metrics(child)
            
        return sorted(all_metrics, key=lambda x: x.duration, reverse=True)[:top_n]

    def log_slow_operations(self, threshold_seconds=1.0, log_level=logging.INFO):
        """Log operations that took longer than the threshold."""
        slow_ops = [m for m in self.get_slowest_operations(100) if m.duration > threshold_seconds]
        
        if slow_ops:
            logger.log(log_level, f"Found {len(slow_ops)} operations exceeding {threshold_seconds}s:")
            for op in slow_ops:
                context_str = ""
                if op.context:
                    context_str = ", ".join(f"{k}={v}" for k, v in op.context.items())
                    context_str = f" ({context_str})"
                logger.log(log_level, f"  {op.name}: {op.duration:.4f}s{context_str}")

    def reset(self) -> None:
        """Reset all performance metrics."""
        self.root_metric = PerformanceMetric("root")
        self.current_metric = self.root_metric
        self._metrics_stack = []


@contextlib.contextmanager
def track_performance(name: str, context: Dict[str, Any] = None):
    """Context manager for tracking performance of a block of code."""
    tracker = PerformanceTracker.get_instance()
    tracker.start_tracking(name, context)
    try:
        yield
    finally:
        tracker.stop_tracking()


def track_method(func=None, *, name=None):
    """Decorator for tracking performance of a method."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            tracker = PerformanceTracker.get_instance()
            
            # For methods, include class name in the tracking name
            if args and hasattr(args[0], '__class__'):
                cls_name = args[0].__class__.__name__
                method_name = name or func.__name__
                tracking_name = f"{cls_name}.{method_name}"
            else:
                tracking_name = name or func.__name__
            
            tracker.start_tracking(tracking_name, {'args_count': len(args), 'kwargs_count': len(kwargs)})
            try:
                result = func(*args, **kwargs)
                return result
            finally:
                tracker.stop_tracking()
        return wrapper
    
    if func is None:
        return decorator
    return decorator(func)


def report_performance(func):
    """Decorator to track and report performance of a function."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        tracker = PerformanceTracker.get_instance()
        tracker.reset()  # Reset tracker before starting
        
        name = func.__name__
        tracker.start_tracking(name)
        try:
            result = func(*args, **kwargs)
            return result
        finally:
            tracker.stop_tracking()
            logger.info("\n" + tracker.generate_report())
            tracker.log_slow_operations(threshold_seconds=0.5)
    
    return wrapper


def track(name: str, context: Dict[str, Any] = None) -> 'PerformanceMetric':
    """
    Simple function to start tracking a section of code.
    
    This provides a simpler alternative to context managers and decorators,
    allowing for one-line tracking statements.
    
    Args:
        name: The name of the section to track
        context: Optional dictionary of context data
        
    Returns:
        The PerformanceMetric object for later reference
        
    Example:
        # Start tracking
        metric = track("load_data")
        
        # Your code here
        data = load_large_file()
        
        # End tracking
        duration = end_track(metric)
        print(f"Loading data took {duration:.4f}s")
    """
    tracker = PerformanceTracker.get_instance()
    return tracker.start_tracking(name, context)


def end_track(metric: Optional['PerformanceMetric'] = None) -> float:
    """
    End tracking for a previously started section.
    
    Args:
        metric: The PerformanceMetric returned from track() 
               (if None, stops the current tracking section)
        
    Returns:
        The duration in seconds
        
    Example:
        # Start and end tracking in one scope
        metric = track("data_processing")
        process_data()
        duration = end_track(metric)
        
        # Track nested operations
        outer = track("outer_operation")
        # ... code ...
        inner = track("inner_operation") 
        # ... more code ...
        inner_time = end_track(inner)
        # ... more code ...
        outer_time = end_track(outer)
    """
    tracker = PerformanceTracker.get_instance()
    stopped = tracker.stop_tracking(metric)
    return stopped.duration if stopped else 0.0