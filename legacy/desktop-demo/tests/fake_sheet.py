"""A drawn stand-in for the Calc window, using the colours and sizes measured in the VM
(1280x800: headers 247, separator lines 105, selected-header blue (67,115,213),
columns 81 px, rows 17 px).  Used to test reading the grid from pixels."""

BACKGROUND, LINE, BLUE, WHITE = (247, 247, 247), (105, 105, 105), (67, 115, 213), (255, 255, 255)


class FakeSheet:
    def __init__(self, width=1280, height=800, col_width=81, row_height=17,
                 header_top=144, header_bottom=158, side_right=31, columns=13, rows=33,
                 realistic=False):
        # realistic=True draws what the recording of the VM showed: the selected header is
        # a gradient, not one flat colour, and its right-hand edge is a blended pixel.
        self.realistic = realistic
        self.width, self.height = width, height
        self.col_width, self.row_height = col_width, row_height
        self.header_top, self.header_bottom, self.side_right = header_top, header_bottom, side_right
        self.columns, self.rows = columns, rows
        self.first_row_top = header_bottom+3
        self.image = [[WHITE]*width for _ in range(height)]
        self.select(0, 0, 1, 1)

    def fill(self, left, top, right, bottom, colour):
        for y in range(max(0, top), min(self.height, bottom+1)):
            line = self.image[y]
            for x in range(max(0, left), min(self.width, right+1)):
                line[x] = colour

    def column_left(self, index):
        return self.side_right+2+index*self.col_width

    def row_top(self, number):
        return self.first_row_top+(number-1)*self.row_height

    def select(self, col1, col2, row1, row2):
        """Redraw both header bands with the given range highlighted."""
        grid_right = self.column_left(self.columns)
        grid_bottom = self.row_top(self.rows+1)
        self.fill(0, self.header_top, grid_right, self.header_bottom, BACKGROUND)
        self.fill(0, self.header_bottom+1, self.side_right, grid_bottom, BACKGROUND)
        self.fill(0, self.header_bottom+1, grid_right, self.header_bottom+2, LINE)
        for index in range(self.columns+1):
            x = self.column_left(index)-1
            self.fill(x, self.header_top, x, self.header_bottom, LINE)
        for number in range(1, self.rows+2):
            y = self.row_top(number)-1
            self.fill(0, y, self.side_right, y, LINE)
        self.fill(self.side_right+1, self.header_top, self.side_right+1, grid_bottom, LINE)
        for index in range(col1, col2+1):
            left = self.column_left(index)
            right = left+self.col_width-2
            self.fill(left, self.header_top, right, self.header_bottom, BLUE)
            if self.realistic:
                middle = (self.header_top+self.header_bottom)//2
                self.fill(left, self.header_top, right, middle, (97, 168, 233))        # lighter upper half
                self.fill(left, middle+1, right, self.header_bottom, (60, 120, 212))
                self.fill(right, self.header_top, right, self.header_bottom, (124, 165, 196))  # blended edge
            self.fill(left+36, self.header_top+4, left+43, self.header_top+11, WHITE)   # the letter
        for number in range(row1, row2+1):
            top = self.row_top(number)
            self.fill(0, top, self.side_right, top+self.row_height-2,
                      (96, 171, 229) if self.realistic else BLUE)
            if self.realistic:
                self.fill(0, top, self.side_right, top, (55, 113, 159))                 # darker first line
            self.fill(12, top+4, 20, top+12, WHITE)                                      # the digit

    def pixel(self, x, y):
        return self.image[int(y)][int(x)]
