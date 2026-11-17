namespace Warehouse;

/// <summary>
/// Audit sink the catalog writes through; the implementation the caller
/// injects decides where the records land.
/// </summary>
public interface IAuditLog
{
    Task RecordAsync(int orderId, int quantity, CancellationToken token);

    Task RecordCancellationAsync(int orderId, string reason, CancellationToken token);
}
