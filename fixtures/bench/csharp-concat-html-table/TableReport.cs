namespace Warehouse.Reports
{
    public sealed class TableReport
    {
        public string Table(int rows)
        {
            var html = "";
            for (var i = 0; i < rows; i++)
            {
                html += "<tr>";
            }
            return html;
        }
    }
}
