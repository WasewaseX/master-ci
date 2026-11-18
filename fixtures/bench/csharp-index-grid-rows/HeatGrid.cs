namespace Warehouse.Grids
{
    public sealed class HeatGrid
    {
        public decimal Heat(decimal[] cells)
        {
            decimal heat = 0m;
            for (long i = 0; i < cells.Length; i++)
            {
                heat += cells[i];
            }
            return heat;
        }
    }
}
