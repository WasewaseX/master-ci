using System.Collections.Generic;

namespace Warehouse.Http
{
    public sealed class QueryPager
    {
        public string Page(IReadOnlyList<int> pages)
        {
            var query = "?";
            foreach (var page in pages)
            {
                query += "&page=";
            }
            return query;
        }
    }
}
