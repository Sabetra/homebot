"""
Batch Processing Utilities
===========================

Utilities for batch processing of embeddings, SQL queries, and other operations.

Functions:
    - batch_embed_texts: Batch embedding of multiple texts
    - batch_sql_load: Load multiple chunks via SQL IN clause
    - batch_insert: Insert multiple items in batches
    - chunk_list: Split list into batches
"""

import logging
from typing import List, Any, Callable, TypeVar, Iterator

logger = logging.getLogger(__name__)

T = TypeVar('T')


def chunk_list(items: List[T], batch_size: int) -> Iterator[List[T]]:
    """
    Split a list into batches of specified size.
    
    Args:
        items: List to split
        batch_size: Size of each batch
        
    Yields:
        Batches of items
        
    Example:
        >>> list(chunk_list([1, 2, 3, 4, 5], 2))
        [[1, 2], [3, 4], [5]]
    """
    for i in range(0, len(items), batch_size):
        yield items[i:i + batch_size]


def batch_embed_texts(
    texts: List[str],
    embedding_function: Callable[[List[str]], List[Any]],
    batch_size: int = 32,
    show_progress: bool = False
) -> List[Any]:
    """
    Embed multiple texts in batches for better performance.
    
    Args:
        texts: List of texts to embed
        embedding_function: Function that takes list of texts and returns list of embeddings
        batch_size: Number of texts to embed at once
        show_progress: Whether to log progress
        
    Returns:
        List of embeddings
        
    Example:
        >>> def embed_fn(texts):
        ...     return [[0.1] * 384 for _ in texts]
        >>> embeddings = batch_embed_texts(["text1", "text2"], embed_fn, batch_size=1)
        >>> len(embeddings)
        2
    """
    if not texts:
        return []
    
    all_embeddings = []
    total_batches = (len(texts) + batch_size - 1) // batch_size
    
    for batch_idx, batch in enumerate(chunk_list(texts, batch_size), 1):
        if show_progress and batch_idx % 10 == 0:
            logger.info(f"⚡ Batch {batch_idx}/{total_batches} - Processing {len(batch)} texts")
        
        try:
            embeddings = embedding_function(batch)
            all_embeddings.extend(embeddings)
        except Exception as e:
            logger.error(f"Batch {batch_idx} failed: {e}")
            # Fallback: embed one by one
            for text in batch:
                try:
                    embedding = embedding_function([text])[0]
                    all_embeddings.append(embedding)
                except Exception as e2:
                    logger.error(f"Failed to embed text: {e2}")
                    # Add zero embedding as placeholder
                    all_embeddings.append(None)
    
    return all_embeddings


def batch_sql_load(
    chunk_ids: List[int],
    conn,
    batch_size: int = 1000,
    table: str = "chunks",
    columns: str = "*"
) -> List[Any]:
    """
    Load multiple rows from database using batched SQL IN clauses.
    
    This is MUCH faster than loading one by one.
    
    Args:
        chunk_ids: List of IDs to load
        conn: Database connection
        batch_size: Maximum IDs per SQL query
        table: Table name to query
        columns: Columns to select
        
    Returns:
        List of rows
        
    Example:
        >>> # Instead of:
        >>> # for id in chunk_ids:
        >>> #     row = conn.execute("SELECT * FROM chunks WHERE rowid=?", (id,))
        >>> 
        >>> # Use:
        >>> rows = batch_sql_load(chunk_ids, conn, batch_size=1000)
    """
    if not chunk_ids:
        return []
    
    all_rows = []
    
    for batch in chunk_list(chunk_ids, batch_size):
        placeholders = ','.join('?' * len(batch))
        query = f"SELECT {columns} FROM {table} WHERE rowid IN ({placeholders})"
        
        cursor = conn.execute(query, batch)
        all_rows.extend(cursor.fetchall())
    
    return all_rows


def batch_insert(
    items: List[Any],
    insert_function: Callable[[List[Any]], None],
    batch_size: int = 100,
    show_progress: bool = False
) -> int:
    """
    Insert multiple items in batches.
    
    Args:
        items: Items to insert
        insert_function: Function that takes a batch of items and inserts them
        batch_size: Number of items to insert at once
        show_progress: Whether to log progress
        
    Returns:
        Number of successfully inserted items
        
    Example:
        >>> def insert_fn(batch):
        ...     conn.executemany("INSERT INTO table VALUES (?)", batch)
        >>> count = batch_insert(items, insert_fn, batch_size=100)
    """
    if not items:
        return 0
    
    total_inserted = 0
    total_batches = (len(items) + batch_size - 1) // batch_size
    
    for batch_idx, batch in enumerate(chunk_list(items, batch_size), 1):
        if show_progress and batch_idx % 10 == 0:
            logger.info(f"⚡ Batch {batch_idx}/{total_batches} - Inserting {len(batch)} items")
        
        try:
            insert_function(batch)
            total_inserted += len(batch)
        except Exception as e:
            logger.error(f"Batch {batch_idx} insert failed: {e}")
            # Try individual inserts
            for item in batch:
                try:
                    insert_function([item])
                    total_inserted += 1
                except Exception as e2:
                    logger.error(f"Failed to insert item: {e2}")
    
    return total_inserted


# Public API
__all__ = [
    'chunk_list',
    'batch_embed_texts',
    'batch_sql_load',
    'batch_insert',
]
