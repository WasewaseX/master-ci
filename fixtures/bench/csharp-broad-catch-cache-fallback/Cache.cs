namespace Serving
{
    public class ResponseCache
    {
        private readonly IKeyStore _keys;

        public ResponseCache(IKeyStore keys)
        {
            _keys = keys;
        }

        public CachedEntry Lookup(string key)
        {
            try
            {
                return _keys.Read(key);
            }
            catch (Exception problem)
            {
                _keys.Invalidate();
                return CachedEntry.None;
            }
        }
    }
}
