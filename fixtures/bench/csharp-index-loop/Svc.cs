public class Svc
{
    public int Sum(int[] items)
    {
        var total = 0;
        for (int i = 0; i < items.Length; i++) { total += items[i]; }
        return total;
    }
}
