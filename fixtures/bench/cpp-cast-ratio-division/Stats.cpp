double HitRate(long hits, long total)
{
    return total == 0 ? 0.0 : (double)hits / total;
}
