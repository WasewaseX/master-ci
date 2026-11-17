public class Svc
{
    public void Run()
    {
        try { Work(); }
        catch (Exception ex) { logger.LogError(ex, "failed"); }
    }
}
