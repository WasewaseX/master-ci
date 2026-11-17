// Geometry over a fixed-size grid; the library owns no output stream.
#pragma once

#include <cstddef>
#include <string>
#include <string_view>
#include <vector>

namespace geometry {

constexpr std::size_t kMaxSide = 4096;

struct Point {
  int x = 0;
  int y = 0;
};

class Grid {
public:
  explicit Grid(std::size_t side);

  std::size_t side() const { return side_; }
  int value(Point p) const;
  void set(Point p, int v);

private:
  std::size_t side_;
  std::vector<int> cells_;
};

std::string describe(const Grid& grid, std::string_view name);

}  // namespace geometry
